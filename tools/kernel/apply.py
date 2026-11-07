#!/usr/bin/env python3
"""Apply or verify the pinned REL3.3 FlexCAN patch, without building or device access."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

PACKAGE = Path(__file__).resolve().parents[2] / "components/flexcan-controller-policy"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError(f"Source path escapes its root or is a symlink: {relative}")
    return path


def apply(source: Path, *, check_only: bool = False) -> str:
    source = source.resolve(strict=True)
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    patch = PACKAGE / manifest["patch"]
    if digest(patch) != manifest["patch_sha256"]:
        raise ValueError("The repository patch does not match its manifest")
    for target, relative in manifest["files"].items():
        if digest(PACKAGE / relative) != manifest["result_sha256"][target]:
            raise ValueError(f"The repository overlay does not match its manifest: {relative}")
    actual = {}
    for relative in manifest["result_sha256"]:
        path = checked_path(source, relative)
        actual[relative] = digest(path) if path.is_file() else None
    if actual == manifest["result_sha256"]:
        return "verified: all seven patched files match"
    if check_only:
        raise ValueError("The prepared source does not match the pinned FlexCAN result")
    for relative, expected in manifest["base_sha256"].items():
        if actual[relative] != expected:
            raise ValueError(f"Refusing to overwrite a changed base file: {relative}")
    for relative in manifest["files"]:
        if actual[relative] is not None:
            raise ValueError(f"Refusing to overwrite a preexisting overlay: {relative}")
    command = ["patch", "--batch", "--forward", "--fuzz=0", "--no-backup-if-mismatch", "-p1", "-i", str(patch)]
    subprocess.run([*command, "--dry-run"], cwd=source, check=True, timeout=30)
    subprocess.run(command, cwd=source, check=True, timeout=30)
    for relative, origin in manifest["files"].items():
        path = checked_path(source, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / origin, path)
        path.chmod(0o644)
    if any(digest(checked_path(source, name)) != expected for name, expected in manifest["result_sha256"].items()):
        raise ValueError("Post-apply identity mismatch; preserve this source for diagnosis")
    return "applied: all seven patched files match"


def energy(source: Path, *, check_only: bool = False) -> dict:
    """Apply only the exact reviewed BQ25601 charger change, without offsets or fuzz."""
    package = PACKAGE.parent / "bq25601-poweroff"
    manifest_path = package / "source.json"
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("schemaVersion") != 1
            or manifest.get("upstreamRevision") != "02cf250c2e6bd375c11dae324ea145a39015d41b"
            or manifest.get("path") != "drivers/power/supply/bq256xx_charger.c"):
        raise ValueError("Unexpected energy driver source contract")
    patch = package / "bq25601-ship-on-poweroff.patch"
    if digest(patch) != manifest["patchSha256"]:
        raise ValueError("Energy driver patch differs from its reviewed manifest")
    driver = checked_path(source, manifest["path"])
    current = digest(driver)
    if current != manifest["postSha256"]:
        if check_only or current != manifest["preSha256"]:
            raise ValueError("Energy driver does not match the exact expected source")
        command = ["patch", "--batch", "--forward", "--fuzz=0", "--no-backup-if-mismatch",
                   "-p1", "-i", str(patch)]
        for arguments in ([*command, "--dry-run"], command):
            result = subprocess.run(arguments, cwd=source, check=True, timeout=30,
                                    capture_output=True, text=True)
            if "offset" in result.stdout.lower() or "fuzz" in result.stdout.lower():
                raise ValueError("Energy patch must apply without offsets or fuzz")
        if digest(driver) != manifest["postSha256"]:
            raise ValueError("Energy driver post-image differs from its reviewed manifest")
    return {"energy_patch_sha256": manifest["patchSha256"],
            "energy_source_manifest_sha256": digest(manifest_path),
            "energy_driver_sha256": digest(driver)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="verify only; never write")
    parser.add_argument("--with-energy", action="store_true", help="also apply/verify the exact reviewed BQ25601 driver patch")
    args = parser.parse_args()
    try:
        print(apply(args.source, check_only=args.check))
        if args.with_energy:
            print(json.dumps(energy(args.source.resolve(strict=True), check_only=args.check), sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"iwave-kernel: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
