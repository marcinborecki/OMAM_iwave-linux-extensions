#!/usr/bin/env python3
"""Prepare portable build inputs from an independently obtained, exact iWave BSP.

No firmware is downloaded or flashed. Only the stock configuration and five
external modules are extracted; the original rootfs and boot files stay in place.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
from pathlib import Path
import shutil
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "image"))
from image_io import archive, digest, write_json
from qualified_inputs import checked_file, document, relative_name, VENDOR_FILES

PROFILE = Path(__file__).resolve().parents[2] / "profiles/tch-g62h-imx91-rel3.3"
STOCK_RELEASE = "6.12.34-lts-next-gb1aec8c1dd0d"
RESERVE = 500 * 1024**2


def configuration(image):
    """Read the bounded embedded IKCONFIG, rather than guessing a defconfig."""
    data = Path(image).read_bytes()
    start, end = data.find(b"IKCFG_ST"), data.find(b"IKCFG_ED")
    if start < 0 or end <= start:
        raise ValueError("Vendor Image lacks its embedded configuration")
    with gzip.GzipFile(fileobj=io.BytesIO(data[start + 8:end])) as source:
        config = source.read(2 * 1024**2 + 1)
    if len(config) > 2 * 1024**2 or not config.startswith(b"#"):
        raise ValueError("Vendor embedded configuration is invalid or too large")
    return config


def prepare(vendor_root, toolchain, output, profile=PROFILE):
    """Bind vendor/toolchain bytes and construct the public kernel baseline contract."""
    vendor_root, output = map(Path, (vendor_root, output))
    toolchain = Path(toolchain) if toolchain is not None else None
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("Preparation output must be new with an existing parent")
    if shutil.disk_usage(output.parent).free < 32 * 1024**2 + RESERVE:
        raise ValueError("Preparation needs 32 MiB plus the 500 MiB reserve")
    lock = document(profile / "vendor-files.lock.json", "Vendor file lock")
    if lock.get("schema") != "iwave.vendor-files.v1" or set(lock.get("files", {})) != set(VENDOR_FILES):
        raise ValueError("Vendor lock must identify all nine BSP inputs")
    for name, record in lock["files"].items():
        checked_file(vendor_root, name, record)
    compiler = None
    if toolchain is not None:
        compiler_lock = document(profile / "build-toolchain.json", "Toolchain lock")
        compiler = toolchain / "usr/bin/aarch64-linux-gnu-gcc-14"
        if not compiler.is_file() or compiler.is_symlink() or digest(compiler) != compiler_lock["compiler_sha256"]:
            raise ValueError("Use the pinned native ARM64 GCC14 toolchain")
    source = document(profile / "source-lock.json", "Vendor source lock")
    gaps = document(profile / "source-gaps.json", "Factory module identities")
    expected_modules = {item["module"]: item["sha256"] for item in gaps["external_modules"]}
    if set(expected_modules) != {"cryptodev.ko", "igb_uio.ko", "kpage_ncache.ko", "mlan.ko", "moal.ko"}:
        raise ValueError("Five factory module identities are required")
    config = configuration(vendor_root / "Linux/Image")
    output.mkdir()
    try:
        config_path = output / "inputs/stock.config"
        config_path.parent.mkdir()
        config_path.write_bytes(config)
        files = {"inputs/vendor/" + name: record for name, record in lock["files"].items()}
        receipt = {"schema": "iwave.rel33.preparation-inputs.v1", "status": "ready",
                   "archive_sha256": lock["archive_sha256"], "files": files,
                   "stock_config_sha256": digest(config_path),
                   "repositories": {"linux": {"revision": source["revision"], "patches": source["patches"]}}}
        patches = output / "inputs/meta-iwave/recipes-kernel/linux/linux-iwg"
        patches.mkdir(parents=True)
        for item in source["patches"]:
            name = relative_name(item["file"])
            path = profile / "vendor-patches" / name
            if not path.is_file() or digest(path) != item["sha256"]:
                raise ValueError("Vendor patch differs from its public source lock")
            (patches / name).symlink_to(path.resolve())
        modules, native, seen = {}, [], set()
        prefix = "usr/lib/modules/" + STOCK_RELEASE + "/"
        with archive(vendor_root / "Linux/rootfs.tar.zst") as stream:
            for member in stream:
                name = member.name.removeprefix("./").rstrip("/")
                if not name.startswith(prefix) or not name.endswith(".ko"):
                    continue
                relative_name(name)
                if name in seen or not member.isfile() or member.issparse() or member.size > 16 * 1024**2:
                    raise ValueError("Stock module must be a unique bounded regular file")
                seen.add(name)
                if name.startswith(prefix + "kernel/"):
                    native.append(name.removeprefix(prefix + "kernel/"))
                    continue
                basename = Path(name).name
                if basename not in expected_modules or basename in modules or not name.startswith(prefix + "updates/"):
                    raise ValueError("Unexpected stock external module")
                destination = output / "baseline/stock-modules" / basename
                destination.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as incoming, destination.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                if digest(destination) != expected_modules[basename]:
                    raise ValueError("Factory module differs from the source-gap identity: " + basename)
                modules[basename] = {"bytes": member.size, "sha256": expected_modules[basename],
                                     "member": name, "retained_path": destination.relative_to(output).as_posix()}
        if len(native) != 807 or set(modules) != set(expected_modules):
            raise ValueError("BSP must provide exactly807 native and5 external modules")
        write_json(output / "baseline/stock-external-modules.json",
                   {"schema": "iwave.stock-modules.v1", "native_count": 807, "native_paths": sorted(native),
                    "rootfs_sha256": lock["files"]["Linux/rootfs.tar.zst"]["sha256"], "files": modules})
        if compiler is not None:
            write_json(output / "toolchain/manifest.json", {"schema": "iwave.toolchain-inputs.v1",
                       "compiler_sha256": digest(compiler), "public_lock_sha256": digest(profile / "build-toolchain.json")})
        write_json(output / "inputs/manifest.json", receipt)
        return {"status": "READY", "native_modules": len(native), "preserved_external_modules": len(modules),
                "preparation_manifest_sha256": digest(output / "inputs/manifest.json"), "device_operations": "NOT_RUN"}
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("Preparation interrupted; owned output is removed")
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("vendor-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--toolchain", type=Path,
                        help="Required for source compilation; omit for restoring an already qualified kernel core")
    args = parser.parse_args()
    print(json.dumps(prepare(args.vendor_root, args.toolchain, args.output)))


if __name__ == "__main__":
    main()
