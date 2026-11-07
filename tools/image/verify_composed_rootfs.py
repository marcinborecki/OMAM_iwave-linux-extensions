#!/usr/bin/env python3
"""Verify every vendor and SDK entry in a composed rootfs without extracting it."""

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile

from compose_rootfs import (PARENTS, PROFILE, RESERVED, check_process, digest_stream,
                            normalized_name, overlay_metadata, overlay_operations, read_overlay, stop_process)
from sdk_payload import tree_sha256


STRUCTURAL_PAX = {"path", "linkpath", "size", "uid", "gid", "uname", "gname", "mtime"}


def file_hash(path):
    with path.open("rb") as content:
        return digest_stream(content)


def archive_entries(path):
    entries = {}
    with path.open("rb") as source, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(["timeout", "--kill-after=5s", "1800s", "zstd", "-q", "-d", "-c"], stdin=source,
                                   stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for member in archive:
                    name = normalized_name(member.name)
                    if name in entries:
                        raise ValueError(f"Duplicate archive entry: {name}")
                    if member.issparse() or any("sparse" in key.lower() for key in member.pax_headers):
                        raise ValueError(f"Sparse archive entry is unsupported: {name}")
                    mtime = Decimal(str(member.mtime))
                    if not mtime.is_finite():
                        raise ValueError(f"Invalid archive timestamp: {name}")
                    record = {"type": member.type.hex(), "uid": member.uid, "gid": member.gid,
                              "uname": member.uname, "gname": member.gname, "mode": member.mode,
                              "mtime": format(mtime.normalize(), "f"), "size": member.size,
                              "linkname": normalized_name(member.linkname) if member.islnk() else member.linkname,
                              "devmajor": member.devmajor, "devminor": member.devminor,
                              "pax": {key: value for key, value in member.pax_headers.items()
                                      if key not in STRUCTURAL_PAX}}
                    if member.isfile():
                        with archive.extractfile(member) as content:
                            record["sha256"] = digest_stream(content)
                    entries[name] = record
            while process.stdout.read(1024 * 1024):
                pass
            check_process(process, errors, "decompression")
        finally:
            stop_process(process)
    return entries


def content_signature(name, entries):
    seen = set()
    while entries[name]["type"] == tarfile.LNKTYPE.hex():
        if name in seen:
            raise ValueError("Cycle in candidate SDK hardlinks")
        seen.add(name)
        name = entries[name]["linkname"]
        if not name.startswith("opt/dotnet/") or name not in entries:
            raise ValueError("Candidate SDK hardlink leaves its payload")
    entry = entries[name]
    if entry["type"] not in (tarfile.REGTYPE.hex(), tarfile.AREGTYPE.hex()):
        raise ValueError("Candidate SDK file resolves to a non-file")
    return entry["size"], entry["sha256"]


def check_injected_metadata(record, mode, epoch, name, ownership=None):
    expected = {"uid": 0, "gid": 0, "uname": "root", "gname": "root", "mode": mode,
                "mtime": epoch, "devmajor": 0, "devminor": 0, "pax": {}}
    if ownership is not None:
        expected.update(ownership)
    if any(record[key] != value for key, value in expected.items()):
        raise ValueError(f"Injected metadata differs: {name}")


def verify(vendor_rootfs, candidate_rootfs, sdk_dir, overlay_dir=None, overlay_plan=None,
           preserve_overlay_ownership=False, profile_path=RESERVED[1]):
    vendor_rootfs, candidate_rootfs, sdk_dir = Path(vendor_rootfs), Path(candidate_rootfs), Path(sdk_dir).resolve()
    report_path = sdk_dir / "sdk-payload-report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("SDK report must be a regular file")
    sdk_report = json.loads(report_path.read_text())
    sdk_hash = tree_sha256(sdk_dir)
    if (sdk_report.get("schema_version") != 1 or sdk_report.get("rid") != "linux-arm64"
            or sdk_report.get("tree_sha256") != sdk_hash):
        raise ValueError("SDK tree fingerprint or report schema does not match")
    deletes, replaces, overlay_entries = read_overlay(overlay_dir, overlay_plan)
    if preserve_overlay_ownership and overlay_dir is None:
        raise ValueError("preserve-overlay-ownership requires an explicitly staged overlay")
    vendor, candidate = archive_entries(vendor_rootfs), archive_entries(candidate_rootfs)
    vendor_types = {name: bytes.fromhex(record["type"]) for name, record in vendor.items()}
    vendor_links = {name: record["linkname"] for name, record in vendor.items() if record["type"] == tarfile.LNKTYPE.hex()}
    operations = overlay_operations(vendor_types, deletes, replaces, overlay_entries, vendor_links)
    for name, record in vendor.items():
        if name in deletes and name in candidate:
            raise ValueError(f"Deleted vendor entry is still present: {name}")
        if name not in deletes | replaces and candidate.get(name) != record:
            raise ValueError(f"Vendor entry changed or missing: {name}")
        if any(name == reserved or name.startswith(reserved + "/") for reserved in RESERVED):
            raise ValueError(f"Vendor owns an SDK injection path: {name}")
        if name in PARENTS and record["type"] != tarfile.DIRTYPE.hex():
            raise ValueError(f"Vendor injection parent is not a directory: {name}")
    if "opt/dotnet" not in candidate:
        raise ValueError("SDK root directory is missing")
    epoch = candidate["opt/dotnet"]["mtime"]
    if Decimal(epoch) < 0 or Decimal(epoch) != Decimal(epoch).to_integral_value():
        raise ValueError("Injected timestamp must be a nonnegative integer")
    expected_names = (set(vendor) - deletes - replaces) | set(operations) | {RESERVED[0], profile_path, RESERVED[2]} | set(PARENTS)
    for name in (*PARENTS, "opt/dotnet"):
        if name in vendor or name in operations:
            continue
        record = candidate.get(name)
        if record is None or record["type"] != tarfile.DIRTYPE.hex() or record["size"] != 0 or record["linkname"]:
            raise ValueError(f"Injected directory missing or invalid: {name}")
        check_injected_metadata(record, 0o755, epoch, name)
    for name, path in operations.items():
        record = candidate.get(name)
        if record is None:
            raise ValueError(f"Overlay entry missing: {name}")
        info = path.lstat()
        mode = info.st_mode
        metadata = overlay_metadata(info, preserve_overlay_ownership)
        check_injected_metadata(record, metadata["mode"], epoch, name, metadata)
        if stat.S_ISREG(mode):
            if (record["type"] != tarfile.REGTYPE.hex() or record["linkname"]
                    or record["size"] != path.stat().st_size or record.get("sha256") != file_hash(path)):
                raise ValueError(f"Overlay file content differs: {name}")
        elif stat.S_ISLNK(mode):
            if record["type"] != tarfile.SYMTYPE.hex() or record["size"] != 0 or record["linkname"] != os.readlink(path):
                raise ValueError(f"Overlay symlink differs: {name}")
        elif record["type"] != tarfile.DIRTYPE.hex() or record["size"] != 0 or record["linkname"]:
            raise ValueError(f"Overlay directory differs: {name}")
    sdk_count = 0
    for parent, directories, files in os.walk(sdk_dir, followlinks=False):
        for local_name in directories + files:
            path = Path(parent) / local_name
            name = "opt/dotnet/" + path.relative_to(sdk_dir).as_posix()
            expected_names.add(name)
            sdk_count += 1
            record = candidate.get(name)
            if record is None:
                raise ValueError(f"SDK entry missing: {name}")
            mode = path.lstat().st_mode
            check_injected_metadata(record, stat.S_IMODE(mode) & 0o777, epoch, name)
            if stat.S_ISREG(mode):
                if content_signature(name, candidate) != (path.stat().st_size, file_hash(path)):
                    raise ValueError(f"SDK file content differs: {name}")
            elif stat.S_ISLNK(mode):
                link = os.readlink(path)
                if Path(link).is_absolute() or not path.resolve().is_relative_to(sdk_dir):
                    raise ValueError(f"SDK symlink leaves its payload: {name}")
                if record["type"] != tarfile.SYMTYPE.hex() or record["linkname"] != link or record["size"] != 0:
                    raise ValueError(f"SDK symlink differs: {name}")
            elif stat.S_ISDIR(mode):
                if record["type"] != tarfile.DIRTYPE.hex() or record["size"] != 0 or record["linkname"]:
                    raise ValueError(f"SDK directory differs: {name}")
            else:
                raise ValueError(f"Unsupported local SDK entry: {name}")
    profile, link = candidate.get(profile_path), candidate.get(RESERVED[2])
    if (profile is None or profile["type"] != tarfile.REGTYPE.hex() or profile["linkname"]
            or profile.get("sha256") != hashlib.sha256(PROFILE).hexdigest() or profile["size"] != len(PROFILE)):
        raise ValueError("Injected dotnet profile differs")
    if (link is None or link["type"] != tarfile.SYMTYPE.hex() or link["linkname"] != "/opt/dotnet/dotnet"
            or link["size"] != 0):
        raise ValueError("Injected dotnet command symlink differs")
    check_injected_metadata(profile, 0o644, epoch, profile_path)
    check_injected_metadata(link, 0o777, epoch, RESERVED[2])
    if set(candidate) != expected_names:
        raise ValueError("Candidate contains unexpected or missing paths")
    def entries_hash(entries):
        return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = {"status": "PASS", "vendor_rootfs_sha256": file_hash(vendor_rootfs),
              "candidate_rootfs_sha256": file_hash(candidate_rootfs), "sdk_tree_sha256": sdk_hash,
              "vendor_entries": len(vendor), "candidate_entries": len(candidate), "sdk_entries": sdk_count,
              "vendor_entries_sha256": entries_hash(vendor), "candidate_entries_sha256": entries_hash(candidate)}
    if overlay_dir is not None:
        result["overlay"] = {"deleted_entries": len(deletes), "replaced_entries": len(replaces),
                             "added_entries": len(operations) - len(replaces)}
        if preserve_overlay_ownership:
            result["overlay"]["ownership"] = "staged-numeric-ids"
    return result


def verify_overlay(base_rootfs, candidate_rootfs, overlay_dir, overlay_plan,
                   preserve_overlay_ownership=False):
    """Check every retained entry, including SDK/kernel bytes, against the accepted base."""
    base, candidate = archive_entries(Path(base_rootfs)), archive_entries(Path(candidate_rootfs))
    deletes, replaces, entries = read_overlay(overlay_dir, overlay_plan)
    types = {name: bytes.fromhex(record['type']) for name, record in base.items()}
    links = {name: record['linkname'] for name, record in base.items() if record['type'] == tarfile.LNKTYPE.hex()}
    operations = overlay_operations(types, deletes, replaces, entries, links)
    expected = (set(base) - deletes - replaces) | set(operations)
    # Existing base images already own the SDK parent directories.
    if set(candidate) != expected:
        raise ValueError('Layered candidate contains unexpected or missing paths')
    for name in set(base) - deletes - replaces:
        if candidate.get(name) != base[name]:
            raise ValueError('Layered candidate changed a retained base entry: ' + name)
    epochs = set()
    for name, path in operations.items():
        record, info = candidate[name], path.lstat()
        epochs.add(record['mtime'])
        metadata = overlay_metadata(info, preserve_overlay_ownership)
        check_injected_metadata(record, metadata['mode'], record['mtime'], name, metadata)
        if stat.S_ISREG(info.st_mode):
            if (record['type'] != tarfile.REGTYPE.hex() or record['size'] != info.st_size
                    or record.get('sha256') != file_hash(path) or record['linkname']):
                raise ValueError('Layered candidate file differs: ' + name)
        elif stat.S_ISLNK(info.st_mode):
            if record['type'] != tarfile.SYMTYPE.hex() or record['linkname'] != os.readlink(path) or record['size']:
                raise ValueError('Layered candidate link differs: ' + name)
        elif record['type'] != tarfile.DIRTYPE.hex() or record['size'] or record['linkname']:
            raise ValueError('Layered candidate directory differs: ' + name)
    if len(epochs) > 1 or any(Decimal(epoch) < 0 or Decimal(epoch) != Decimal(epoch).to_integral_value() for epoch in epochs):
        raise ValueError('Layered overlay timestamps must be one nonnegative integer')
    return {'status': 'PASS', 'base_rootfs_sha256': file_hash(Path(base_rootfs)),
        'candidate_rootfs_sha256': file_hash(Path(candidate_rootfs)),
        'retained_entries': len(set(base) - deletes - replaces), 'overlay_entries': len(operations),
        'sdk_and_unmodified_kernel': 'byte and metadata identical to base'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("vendor-rootfs", "candidate-rootfs", "sdk-dir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--overlay-dir", type=Path)
    parser.add_argument("--overlay-plan", type=Path)
    parser.add_argument("--preserve-overlay-ownership", action="store_true",
                        help="verify prepared target-root numeric ownership and directory special mode bits")
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError("Verification report output must not exist")
        report = verify(args.vendor_rootfs, args.candidate_rootfs, args.sdk_dir, args.overlay_dir, args.overlay_plan,
                        args.preserve_overlay_ownership)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, tarfile.TarError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
