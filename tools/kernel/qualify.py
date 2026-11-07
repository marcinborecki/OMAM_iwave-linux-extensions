#!/usr/bin/env python3
"""Assemble and statically qualify one native kernel/module set; no device access."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from build import REPO, RESERVE, constraints, identity, sha, write_json, interrupted, resource_arguments
from process_group import stop
from source_identity import relative_name, strict_document
from module_abi import (ElfModule, check_versions, check_vermagic, supported_relocations,
                        check_namespaces, gpl_compatible_licenses, check_gpl_imports)


def load_image_parser():
    from image_exports import symbol_table
    return symbol_table, sha(Path(__file__).with_name("image_exports.py"))


def verify_imu_package(directory: Path, release: str) -> None:
    """Check the public package contract without importing an application installer."""
    if any(path.is_symlink() for path in directory.iterdir()):
        raise ValueError("IMU package differs from the regular-file contract")
    manifest = json.loads((directory / "manifest.json").read_text())
    modules = manifest["modules"]
    expected = {"st_lsm6dsx.ko", "st_lsm6dsx_i2c.ko"}
    if (manifest.get("schema") != "omam.imu-modules.v1"
            or manifest.get("kernelRelease") != release
            or len(modules) != 2 or {item["file"] for item in modules} != expected
            or {p.name for p in directory.iterdir()} != expected | {"manifest.json"}):
        raise ValueError("IMU package names, release or manifest differ from the contract")
    for item in modules:
        path = directory / item["file"]
        if path.is_symlink() or not path.is_file() or sha(path) != item["sha256"]:
            raise ValueError("IMU module differs from its manifest: " + item["file"])


def native_baseline_names(baseline: Path, vendor_inputs: dict, build_inputs: dict):
    """Use a source-bound compact inventory or the unchanged legacy module-tree route."""
    stock_receipt, inventory_hash, _ = strict_document(baseline / "baseline/stock-external-modules.json", 8 * 1024**2)
    if "native_paths" in stock_receipt:
        rootfs_hash = vendor_inputs.get("files", {}).get("inputs/vendor/Linux/rootfs.tar.zst", {}).get("sha256")
        paths = stock_receipt["native_paths"]
        count = stock_receipt.get("native_count")
        if (build_inputs.get("stock_module_inventory_sha256") != inventory_hash
                or not isinstance(rootfs_hash, str) or not re.fullmatch("[a-f0-9]{64}", rootfs_hash)
                or stock_receipt.get("rootfs_sha256") != rootfs_hash):
            raise ValueError("Compact module inventory differs from its build or stock-rootfs identity")
        if (type(count) is not int or count <= 0 or not isinstance(paths, list)
                or len(paths) != count or any(not isinstance(name, str) for name in paths)):
            raise ValueError("Compact module inventory must declare every native module path")
        for name in paths:
            relative_name(name)
            if not name.endswith(".ko"):
                raise ValueError("Compact native inventory contains a non-module path")
        if len(set(paths)) != count:
            raise ValueError("Compact native inventory contains duplicate module paths")
        return stock_receipt, set(paths)
    old_roots = list((baseline / "baseline/kernel-set/lib/modules").glob("*/kernel"))
    if len(old_roots) != 1:
        raise ValueError("Need exactly one accepted baseline in-tree module set")
    return stock_receipt, {p.relative_to(old_roots[0]).as_posix() for p in old_roots[0].rglob("*.ko")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "baseline", "toolchain", "work", "output", "receipt"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, help="the same published source-manifest.json used for compilation")
    resource_arguments(parser)
    args = parser.parse_args()
    for name in ("source", "baseline", "toolchain", "work", "output", "receipt"):
        setattr(args, name, getattr(args, name).resolve())
    group, resources = constraints(args)
    receipt = json.loads(args.receipt.read_text())
    if receipt["status"] != "KERNEL_COMPILED":
        parser.error("Need a completed full build receipt for this candidate")
    release = receipt["inputs"]["kernel_release"]
    vendor_inputs = json.loads((args.baseline / "inputs/manifest.json").read_text())
    if sha(args.baseline / "inputs/stock.config") != vendor_inputs["stock_config_sha256"]:
        parser.error("Stock configuration differs from the pinned vendor input receipt")
    for patch_input in vendor_inputs["repositories"]["linux"]["patches"]:
        patch_path = args.baseline / "inputs/meta-iwave/recipes-kernel/linux/linux-iwg" / patch_input["file"]
        if sha(patch_path) != patch_input["sha256"]:
            parser.error("Vendor patch differs from its acquisition receipt: " + patch_input["file"])
    if identity(args.source, args.baseline, args.toolchain, release, args.source_manifest) != receipt["inputs"]:
        parser.error("Build inputs changed since the candidate compilation")
    if args.output.exists():
        parser.error("Use an absent candidate output directory; preserve or explicitly retire existing output")
    if shutil.disk_usage(args.work).free < 600 * 1024**2 + RESERVE:
        parser.error("Need module installation outputs plus the 500 MiB reserve")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env["PATH"] = str(args.toolchain / "usr/bin") + ":" + env["PATH"] + ":/usr/sbin:/sbin"
    env["LD_LIBRARY_PATH"] = str(args.toolchain / "usr/lib/aarch64-linux-gnu")
    env["BISON_PKGDATADIR"] = str(args.toolchain / "usr/share/bison")
    env["M4"] = str(args.toolchain / "usr/bin/m4")
    make = ["make", "-C", str(args.source), "O=" + str(args.work), "ARCH=arm64",
            "CC=" + str(args.toolchain / "usr/bin/aarch64-linux-gnu-gcc-14"), "HOSTCC=/usr/bin/gcc",
            "HOSTCFLAGS=-O2 -I" + str(args.toolchain / "usr/include"),
            "HOSTLDFLAGS=-L" + str(args.toolchain / "usr/lib/aarch64-linux-gnu") +
            " -Wl,-rpath," + str(args.toolchain / "usr/lib/aarch64-linux-gnu"),
            "KERNELRELEASE=" + release, "KCFLAGS=-ffile-prefix-map=" + str(args.baseline) + "/=", "-j" + str(args.jobs)]
    steps = []
    report = {"status": "RUNNING", "commands": steps, "tool_sha256": sha(Path(__file__)),
              "elf_parser_sha256": sha(Path(__file__).with_name("module_abi.py")),
              "process_supervisor_sha256": sha(Path(__file__).with_name("process_group.py")),
              "physical_loading": "NOT_RUN", "hardware_readback": "NOT_RUN", "resources": resources}
    logs = args.work / "qualification.log"
    logs.unlink(missing_ok=True)

    def run(label, command, timeout=120, capture=False, quiet_required=False):
        began = time.monotonic()
        step = {"phase": label, "argv": list(map(str, command)), "timeout_seconds": timeout}
        steps.append(step)
        report["current_phase"] = label
        receipt["qualification"] = report
        write_json(args.receipt, receipt)
        out, err, failure = b"", b"", None
        with logs.open("ab") as log:
            child = subprocess.Popen(command, env=env, stdout=subprocess.PIPE if capture else log,
                                     stderr=subprocess.PIPE if capture else subprocess.STDOUT, start_new_session=True)
            try:
                out, err = child.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                failure = error
            finally:
                stop(child)
                step["exit_code"] = child.returncode
                step["elapsed_seconds"] = round(time.monotonic() - began, 3)
        if capture:
            step.update(stdout_sha256=hashlib.sha256(out or b"").hexdigest(),
                        stderr=(err or b"").decode(errors="replace")[-3000:])
        if failure:
            raise TimeoutError(label + " exceeded its bounded deadline") from failure
        if child.returncode or (quiet_required and (out or err)):
            raise ValueError(label + " failed: " + str(step))
        return out if capture else b""

    try:
        receipt["status"] = "QUALIFYING"
        receipt["qualification"] = report
        write_json(args.receipt, receipt)
        args.output.mkdir(parents=True)
        for relative, target in [("arch/arm64/boot/Image", "Image"),
                                 ("arch/arm64/boot/dts/freescale/imx91-iwg62h-prgtt.dtb", "imx91-iwg62h-prgtt.dtb"),
                                 ("System.map", "System.map"), ("Module.symvers", "Module.symvers"), (".config", "config")]:
            shutil.copy2(args.work / relative, args.output / target)
        run("modules_install", [*make, "INSTALL_MOD_PATH=" + str(args.output), "INSTALL_MOD_STRIP=1",
                                "DEPMOD=/usr/sbin/depmod", "modules_install"], 600)
        module_root = args.output / "lib/modules" / release
        for name in ("build", "source"):
            path = module_root / name
            if path.is_symlink():
                path.unlink()  # The shipped set must not point at a host build directory.
        native = sorted(module_root.rglob("*.ko"))
        if not native:
            raise ValueError("No in-tree modules installed")
        stock_receipt, old_names = native_baseline_names(args.baseline, vendor_inputs, receipt["inputs"])
        new_names = {p.relative_to(module_root / "kernel").as_posix() for p in native}
        if new_names != old_names or len(native) != stock_receipt["native_count"]:
            raise ValueError("In-tree module names changed from the pinned baseline")
        stock_targets = {}
        for name, original in stock_receipt["files"].items():
            source = args.baseline / original["retained_path"]
            if sha(source) != original["sha256"]:
                raise ValueError("Changed stock external module: " + name)
            relative = original["member"].split("/updates/", 1)[1]
            target = module_root / "updates" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            stock_targets[target.relative_to(args.output).as_posix()] = original
        if len(stock_targets) != 5:
            raise ValueError("Expected all five stock external modules")

        # Build only the required upstream core/I2C front end as external modules.
        # Their sources do not depend on CONFIG_IIO_ST_LSM6DSX; their explicit
        # obj-m list uses the already compiled candidate's exact headers/symvers.
        imu = args.work / "imu-src"
        imu.mkdir()
        imu_sources = {}
        for path in sorted((args.source / "drivers/iio/imu/st_lsm6dsx").glob("*.[ch]")):
            shutil.copy2(path, imu / path.name)
            imu_sources[path.name] = sha(path)
        patch = REPO / "components/st-lsm6dsx-existing-irq/omam-st_lsm6dsx_i2c-existing-irq.patch"
        if (sha(patch) != "e0fcbb24dc4b41b1842b2431e264967a6ff9d93957499c76eef0b294b5bc848e"
                or sha(imu / "st_lsm6dsx_i2c.c") != "2153bdfecdb45afd95e002a6a5ebb4d07e9357c100221c6dbba13b925029bb1c"):
            raise ValueError("IMU source or patch differs from its qualified input")
        patch_output = run("imu_irq_patch", ["patch", "--batch", "--forward", "--fuzz=0", "--no-backup-if-mismatch", "-d", str(imu),
                              "-p5", "-i", str(patch)], 30, capture=True)
        if (b"offset" in patch_output.lower() or b"fuzz" in patch_output.lower()
                or sha(imu / "st_lsm6dsx_i2c.c") != "d5669149e26f081d9d96c3617bf07559bcca56dfdb6186ccd770d0294d71f5d5"):
            raise ValueError("IMU patch must produce the exact qualified result without offsets or fuzz")
        (imu / "Makefile").write_text("st_lsm6dsx-y := st_lsm6dsx_core.o st_lsm6dsx_buffer.o st_lsm6dsx_shub.o\n"
                                     "obj-m += st_lsm6dsx.o st_lsm6dsx_i2c.o\n")
        imu_build_inputs = {path.name: sha(path) for path in sorted(imu.iterdir()) if path.is_file()}
        run("imu_modules", [*make, "M=" + str(imu), "modules"], 300)
        imu_target = module_root / "updates/omam"
        imu_target.mkdir(parents=True)
        for name in ("st_lsm6dsx.ko", "st_lsm6dsx_i2c.ko"):
            shutil.copy2(imu / name, imu_target / name)
            run("strip_imu", ["strip", "--strip-debug", str(imu_target / name)], 30)

        image = (args.output / "Image").read_bytes()
        required_driver_symbols = {"flexcan_omam_apply", "flexcan_omam_program", "flexcan_omam_family_init",
                                   "bq25601_ship_shutdown"}
        map_symbols = {line.split()[-1] for line in (args.output / "System.map").read_text().splitlines()}
        if (not required_driver_symbols <= map_symbols or b"omam_flexcan\0" not in image
                or b"ship_mode_on_poweroff\0" not in image
                or "CONFIG_CHARGER_BQ256XX=y" not in (args.output / "config").read_text().splitlines()):
            raise ValueError("The final kernel lacks the compiled controller or BQ25601 energy backend")
        report["driver_symbols"] = sorted(required_driver_symbols)
        report["build_identity"] = {
            "compile_h_sha256": sha(args.work / "include/generated/compile.h"),
            "utsrelease_h_sha256": sha(args.work / "include/generated/utsrelease.h"),
            "build_version": (args.work / ".version").read_text().strip(),
            "repeated_byte_reproducibility": "NOT_RUN; source/tool recipe and actual build banner retained",
        }
        start, end = image.find(b"IKCFG_ST"), image.find(b"IKCFG_ED")
        if start < 0 or end <= start or gzip.decompress(image[start + 8:end]) != (args.output / "config").read_bytes():
            raise ValueError("Candidate Image IKCONFIG does not match the compiled config")
        banner = re.search(rb"Linux version [^\0\n]+", image)
        if not banner or not banner.group().startswith(("Linux version " + release + " ").encode()):
            raise ValueError("Candidate Image does not carry its declared kernel release")
        image_parser, parser_hash = load_image_parser()
        image_table = image_parser(image)
        exports = {name: crc for crc, name, gpl, ns in image_table}
        namespaces = {name: ns for crc, name, gpl, ns in image_table}
        gpl_only = {name for crc, name, gpl, ns in image_table if gpl}
        compatible_licenses = gpl_compatible_licenses(args.source)
        symvers = {}
        for line in (args.output / "Module.symvers").read_text().splitlines():
            fields = line.split()
            symvers[fields[1]] = (int(fields[0], 16), fields[2])
        expected_image = {name: crc for name, (crc, owner) in symvers.items() if owner == "vmlinux"}
        if exports != expected_image:
            raise ValueError("Image export table and compiled vmlinux Module.symvers disagree")
        report.update(image_export_count=len(exports), image_parser_sha256=parser_hash,
                      image_banner=banner.group().decode(), ikconfig="byte-identical",
                      stock_module_receipt_sha256=sha(args.baseline / "baseline/stock-external-modules.json"))

        all_modules = sorted(module_root.rglob("*.ko"))
        if len(all_modules) != len(native) + 7:
            raise ValueError("The assembled module set must contain all native, five stock and two IMU modules")
        parsed = {p: ElfModule(p) for p in all_modules}
        layouts = {parsed[p].layout_size() for p in native}
        if len(layouts) != 1:
            raise ValueError("Compiled modules disagree on struct module size")
        layout = layouts.pop()
        reference_magic = parsed[native[0]].metadata()["vermagic"][0]
        if reference_magic.split()[0] != release:
            raise ValueError("New in-tree modules have the wrong release")
        supported = supported_relocations(args.source)
        providers = {name: "vmlinux" for name in exports}
        native_paths = set(native)
        for path, module in parsed.items():
            for name, crc in module.exports().items():
                if name in exports:
                    raise ValueError("Duplicate exported symbol: " + name)
                if path in native_paths and (name not in symvers or symvers[name][0] != crc):
                    raise ValueError("Compiled module exports disagree with Module.symvers: " + name)
                exports[name] = crc
                providers[name] = path.relative_to(args.output).as_posix()
            own_namespaces, own_gpl = module.export_contracts()
            namespaces.update(own_namespaces)
            gpl_only.update(own_gpl)
        imports_checked = 0
        details = {}
        for path, module in parsed.items():
            relative = path.relative_to(args.output).as_posix()
            versions = module.versions()
            check_versions(versions, exports)
            metadata = module.metadata()
            check_namespaces(versions, namespaces, metadata.get("import_ns", []))
            check_gpl_imports(versions, gpl_only, metadata.get("license", []), compatible_licenses)
            magic = metadata["vermagic"][0]
            check_vermagic(magic, reference_magic, preserved_stock=relative in stock_targets, has_crcs=bool(versions))
            if module.layout_size() != layout:
                raise ValueError("Module layout mismatch: " + relative)
            unsupported = module.relocations() - supported.keys()
            if unsupported:
                raise ValueError("Unsupported relocations: " + relative + " " + repr(unsupported))
            imports_checked += len(versions)
            details[relative] = {"bytes": path.stat().st_size, "sha256": sha(path), "vermagic": magic,
                                 "versioned_imports": len(versions), "module_layout_bytes": module.layout_size()}
            if relative in stock_targets and details[relative]["sha256"] != stock_targets[relative]["sha256"]:
                raise ValueError("Stock module bytes changed after preservation")
        aggregate = args.output / "module-symbols.symvers"
        aggregate.write_text("".join(f"0x{exports[name]:08x}\t{name}\t{providers[name]}\tEXPORT_SYMBOL\n"
                                     for name in sorted(exports)))
        run("depmod_versioned", ["/usr/sbin/depmod", "-e", "-E", str(aggregate), "-b", str(args.output), release],
            120, capture=True, quiet_required=True)
        run("depmod_system_map", ["/usr/sbin/depmod", "-e", "-F", str(args.output / "System.map"),
                                  "-b", str(args.output), release], 120, capture=True, quiet_required=True)
        dependencies = {}
        for name in ("cryptodev", "igb_uio", "kpage_ncache", "mlan", "moal", "st_lsm6dsx_i2c"):
            dependencies[name] = run("resolve_" + name, ["/usr/sbin/modprobe", "-n", "--show-depends",
                                     "-d", str(args.output), "-S", release, "-C", "/dev/null", name],
                                     capture=True).decode().splitlines()
            if not dependencies[name] or any(not line.startswith("insmod " + str(module_root)) for line in dependencies[name]):
                raise ValueError("Unexpected module dependency resolution: " + name)
        dtb = run("dtb_readback", [str(args.work / "scripts/dtc/dtc"), "-I", "dtb", "-O", "dts",
                                  str(args.output / "imx91-iwg62h-prgtt.dtb")], 60, capture=True).decode()
        if dtb.count('compatible = "fsl,imx93-flexcan"') != 2 or 'compatible = "st,lsm6dsl"' not in dtb:
            raise ValueError("Candidate DTB lost qualified CAN/IMU device identities")
        report.update(status="PASS_BUILD_AND_STATIC_ABI", native_modules=len(native), stock_modules=5, imu_modules=2,
                      total_modules=len(all_modules), versioned_imports_checked=imports_checked, module_layout_bytes=layout,
                      import_namespaces="all declared", gpl_only_imports="all loader-compatible",
                      dependencies=dependencies,
                      special_modules={name: value for name, value in details.items()
                                       if "/updates/" in name},
                      dtb_readback={"sha256": hashlib.sha256(dtb.encode()).hexdigest(), "flexcan_controllers": 2,
                                    "imu_compatible": "st,lsm6dsl"},
                      imu={"source_files": imu_sources, "compiled_files": imu_build_inputs,
                           "patch_sha256": sha(patch), "route": "external obj-m core and I2C; candidate config and Module.symvers"},
                      loader_source_sha256=sha(args.source / "kernel/module/version.c"),
                      loader_main_sha256=sha(args.source / "kernel/module/main.c"),
                      loader_license_sha256=sha(args.source / "include/linux/license.h"),
                      relocation_source_sha256=sha(args.source / "arch/arm64/kernel/module.c"))
        imu_manifest = {"schema": "omam.imu-modules.v1", "kernelRelease": release, "vermagic": reference_magic,
                        "kernelImageSha256": sha(args.output / "Image"), "source": {"srcrev": receipt["inputs"]["srcrev"],
                        "omamPatch": {"file": patch.name, "sha256": sha(patch)},
                        "originalFiles": imu_sources, "compiledFiles": imu_build_inputs},
                        "build": {"tool": "tools/kernel/qualify.py", "configurationSha256": sha(args.output / "config")},
                        "checks": {"vermagic": "equal", "thisModuleSize": hex(layout), "relocations": "supported",
                                   "vmlinuxImportsWithImageCrc": sum(providers[symbol] == "vmlinux"
                                       for name in ("st_lsm6dsx.ko", "st_lsm6dsx_i2c.ko")
                                       for symbol in parsed[imu_target / name].versions())},
                        "modules": [{"file": name, "name": name[:-3], "sha256": sha(imu_target / name),
                                     "bytes": (imu_target / name).stat().st_size, "vermagic": reference_magic,
                                     "depends": list(filter(None, parsed[imu_target / name].metadata().get("depends", []))),
                                     "importNs": list(filter(None, parsed[imu_target / name].metadata().get("import_ns", []))),
                                     "srcversion": None, "versionedImports": len(parsed[imu_target / name].versions())}
                                    for name in ("st_lsm6dsx.ko", "st_lsm6dsx_i2c.ko")]}
        write_json(imu_target / "manifest.json", imu_manifest)
        verify_imu_package(imu_target, release)
        report["imu_package_contract"] = {"implementation": "standalone manifest, exact names and hashes", "status": "PASS"}
        report["outputs"] = {name: {"bytes": (args.output / name).stat().st_size, "sha256": sha(args.output / name)}
                             for name in ("Image", "imx91-iwg62h-prgtt.dtb", "config", "Module.symvers", "System.map", "module-symbols.symvers")}
        module_metadata = {}
        for path in sorted(module_root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Shipped module set contains a symlink: " + path.relative_to(args.output).as_posix())
            if path.is_file() and path.suffix != ".ko":
                module_metadata[path.relative_to(args.output).as_posix()] = {
                    "bytes": path.stat().st_size, "sha256": sha(path)}
            elif not path.is_file() and not path.is_dir():
                raise ValueError("Shipped module set contains a non-regular entry")
        write_json(args.output / "kernel-manifest.json", {"schema": "omam.kernel-set.v1", "release": release,
                   "inputs": receipt["inputs"], "outputs": report["outputs"],
                   "module_vermagic": reference_magic, "module_layout_bytes": layout,
                   "module_metadata": module_metadata,
                   "modules": {name: {k: value[k] for k in ("bytes", "sha256", "versioned_imports")}
                               for name, value in details.items()},
                   "stock_external_provenance": {"rootfs_sha256": stock_receipt["rootfs_sha256"],
                                                 "files": stock_targets},
                   "physical_loading": "NOT_RUN", "static_qualification": "PASS_BUILD_AND_STATIC_ABI"})
        report["kernel_manifest"] = {"path": str(args.output / "kernel-manifest.json"),
                                     "sha256": sha(args.output / "kernel-manifest.json")}
        report.pop("current_phase", None)
        receipt["status"] = report["status"]
        return 0
    except BaseException as error:
        report["status"] = "FAILED"
        report["error"] = str(error)
        receipt["status"] = "QUALIFICATION_FAILED"
        print(str(error), file=sys.stderr, flush=True)
        return 1
    finally:
        for name in ("memory.peak", "memory.events", "cpu.stat"):
            report["resources"][name] = (group / name).read_text().strip()
        receipt["qualification"] = report
        write_json(args.receipt, receipt)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(main())
