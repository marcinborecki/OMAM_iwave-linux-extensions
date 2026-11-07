#!/usr/bin/env python3
"""Read a vendor rootfs without extracting it; estimate space for lab additions."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile

MIB = 1024 * 1024
BLOCK = 4096


def rounded(size):
    return ((size + BLOCK - 1) // BLOCK) * BLOCK


def archive_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive member: {name}")
    return str(path)


def inspect_archive(path):
    """Count regular-file data once per final path; hard links add no file data."""
    path = Path(path)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    files = {}
    metadata = {}
    libraries = set()
    executables = set()
    duplicates = 0
    process = subprocess.Popen(["zstd", "-dc", "--", str(path)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                name = archive_name(member.name)
                if name in files:
                    duplicates += 1
                kind = ("file" if member.isfile() else "directory" if member.isdir()
                        else "hardlink" if member.islnk() else "symlink" if member.issym()
                        else "special")
                files[name] = (kind, member.size if member.isfile() else 0)
                if name in ("etc/os-release", "usr/lib/os-release", "etc/issue"):
                    if member.isfile() and member.size <= 16384:
                        metadata[name] = archive.extractfile(member).read().decode("utf-8", "replace")
                basename = PurePosixPath(name).name
                if re.match(r"(?:libc(?:[-.]|$)|libstdc\+\+|libgcc_s|libssl|libcrypto|libicu|libz[.-]|ld-linux|libgssapi)", basename):
                    libraries.add(name)
                if basename in ("mc", "sshd", "dropbear", "dotnet", "rpm", "opkg", "dnf", "apt", "gdbserver"):
                    if "/bin/" in "/" + name or "/sbin/" in "/" + name:
                        executables.add(name)
                # Streaming inventory does not need tarfile's growing member cache.
                archive.members.clear()
        # Consume the compressed frame to its end so zstd checks its integrity.
        while process.stdout.read(1024 * 1024):
            pass
        process.stdout.close()
        error = process.stderr.read().decode("utf-8", "replace")
        process.stderr.close()
        if process.wait() != 0:
            raise ValueError(f"Rootfs decompression failed: {error.strip()}")
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()
        raise
    counts = {kind: sum(item[0] == kind for item in files.values())
              for kind in ("file", "directory", "hardlink", "symlink", "special")}
    return {
        "sha256": digest,
        "compressed_bytes": path.stat().st_size,
        "regular_file_bytes": sum(size for kind, size in files.values()),
        "regular_file_4k_bytes": sum(rounded(size) for kind, size in files.values()),
        "member_counts": counts,
        "duplicate_member_paths": duplicates,
        "metadata": metadata,
        "library_paths": sorted(libraries),
        "tool_paths": sorted(executables),
        "note": "Archive inventory, not an installed-filesystem or device test. Library names do not prove ABI compatibility.",
    }


def directory_size(path):
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise ValueError("SDK directory must be an existing real directory")
    total = blocks = count = 0
    inodes = set()
    for directory, _, names in os.walk(path, followlinks=False):
        for name in names:
            info = (Path(directory) / name).lstat()
            key = (info.st_dev, info.st_ino)
            if stat.S_ISREG(info.st_mode) and key not in inodes:
                inodes.add(key)
                total += info.st_size
                blocks += rounded(info.st_size)
                count += 1
    return {"regular_file_bytes": total, "regular_file_4k_bytes": blocks, "regular_files": count}


def rootfs_offset(script):
    text = Path(script).read_text(encoding="utf-8")
    layouts = re.findall(r"PARTSTR=\$'(\d+)M,(\d+)M,0c\\n(\d+)M,,83\\n';", text)
    if len(layouts) != 1:
        raise ValueError("Unrecognised partition layout; review the new BSP and pass --rootfs-start-mib explicitly")
    boot_start, boot_size, root_start = map(int, layouts[0])
    if root_start < boot_start + boot_size:
        raise ValueError("Overlapping vendor partition layout")
    return root_start * MIB


def capacity(disk_bytes, offset, data_bytes, extra_bytes, workspace_bytes, allowance_percent):
    if disk_bytes <= offset:
        raise ValueError("Disk does not reach the rootfs start")
    partition = disk_bytes - offset
    allowance = math.ceil(partition * allowance_percent / 100)
    remaining = partition - allowance - data_bytes - extra_bytes - workspace_bytes
    return {
        "disk_user_area_bytes": disk_bytes,
        "rootfs_partition_bytes": partition,
        "filesystem_allowance_bytes": allowance,
        "extra_packages_budget_bytes": extra_bytes,
        "workspace_budget_bytes": workspace_bytes,
        "remaining_after_budgets_bytes": remaining,
        "fits_estimate": remaining >= 0,
        "device_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--rootfs", type=Path)
    source.add_argument("--inventory", type=Path, help="Reuse a previous report for the SAME vendor rootfs SHA256")
    layout = parser.add_mutually_exclusive_group(required=True)
    layout.add_argument("--uuu-script", type=Path)
    layout.add_argument("--rootfs-start-mib", type=int)
    parser.add_argument("--sdk-dir", type=Path)
    parser.add_argument("--disk-bytes", type=int, action="append", help="User-area bytes; repeat for capacity scenarios")
    parser.add_argument("--extra-mib", type=int, default=128, help="Unmeasured tools/debugger budget")
    parser.add_argument("--workspace-mib", type=int, default=1024, help="Application/build/NuGet/log budget")
    parser.add_argument("--filesystem-allowance-percent", type=int, default=10,
                        help="Estimate for metadata, reserved blocks and uncertainty; not a measured overhead")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.extra_mib < 0 or args.workspace_mib < 0 or not 0 <= args.filesystem_allowance_percent < 100:
        parser.error("Budgets must be non-negative and allowance must be in 0..99")
    offset = rootfs_offset(args.uuu_script) if args.uuu_script else args.rootfs_start_mib * MIB
    if offset < 0:
        parser.error("Rootfs offset cannot be negative")
    for path in (args.rootfs, args.inventory, args.uuu_script):
        if path and path.resolve() == args.output.resolve():
            parser.error("Report must not overwrite an input")
    vendor = (inspect_archive(args.rootfs) if args.rootfs else
              json.loads(args.inventory.read_text(encoding="utf-8"))["vendor_rootfs"])
    sdk = directory_size(args.sdk_dir) if args.sdk_dir else {"regular_file_bytes": 0, "regular_file_4k_bytes": 0}
    data_bytes = vendor["regular_file_4k_bytes"] + sdk["regular_file_4k_bytes"]
    report = {
        "schema_version": 1,
        "vendor_rootfs": vendor,
        "sdk_payload": sdk,
        "rootfs_start_bytes": offset,
        "combined_regular_file_4k_bytes": data_bytes,
        "filesystem_allowance_percent": args.filesystem_allowance_percent,
        "capacity_scenarios": [capacity(size, offset, data_bytes, args.extra_mib * MIB,
                                        args.workspace_mib * MIB, args.filesystem_allowance_percent)
                               for size in (args.disk_bytes or [8_000_000_000, 16_000_000_000])],
        "limitations": ["Physical device capacity and RAM have not been measured.",
                        "Tool/debugger and working-space amounts are budgets, not installed payload measurements.",
                        "No rootfs extraction, modification, flashing or target execution was performed."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "rootfs_file_bytes": vendor["regular_file_bytes"],
                      "sdk_file_bytes": sdk["regular_file_bytes"],
                      "capacity_scenarios": report["capacity_scenarios"]}, indent=2))


if __name__ == "__main__":
    main()
