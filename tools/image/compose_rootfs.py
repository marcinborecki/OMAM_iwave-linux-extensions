#!/usr/bin/env python3
"""Compose a new lab rootfs archive without extraction, root privileges, or device access."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile

from sdk_payload import tree_sha256


PARENTS = ("opt", "etc", "etc/profile.d", "usr", "usr/local", "usr/local/bin")
RESERVED = ("opt/dotnet", "etc/profile.d/omam-dotnet.sh", "usr/local/bin/dotnet")
SDK_PROTECTED = (*RESERVED, "etc/profile.d/dotnet.sh")
PROFILE = b'export DOTNET_ROOT=/opt/dotnet\nexport PATH="$DOTNET_ROOT:$PATH"\n'


def read_overlay(overlay_dir, overlay_plan):
    """Read explicit mutations and discover local entries without following symlinks."""
    if overlay_dir is None and overlay_plan is None:
        return set(), set(), {}
    if overlay_dir is None or overlay_plan is None:
        raise ValueError("overlay-dir and overlay-plan must be provided together")
    root = Path(overlay_dir).resolve()
    if not root.is_dir():
        raise ValueError("Overlay directory does not exist")
    plan = json.loads(Path(overlay_plan).read_text())
    if not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1:
        raise ValueError("Overlay plan requires schema_version 1")
    changes = []
    for operation in ("delete", "replace"):
        paths = plan.get(operation)
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError("Overlay delete and replace must be path lists")
        names = {normalized_name(path) for path in paths}
        if "." in names or len(names) != len(paths):
            raise ValueError("Overlay plan contains a root mutation or duplicate path")
        changes.append(names)
    deletes, replaces = changes
    if deletes & replaces:
        raise ValueError("Overlay delete and replace paths conflict")
    def walk_error(error):
        raise error
    entries = {}
    for parent, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        for local_name in directories + files:
            path = Path(parent) / local_name
            name = normalized_name(path.relative_to(root).as_posix())
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise ValueError(f"Unsupported overlay entry: {name}")
            entries[name] = path
    for name in deletes | replaces | entries.keys():
        if any(name == reserved or name.startswith(reserved + "/") for reserved in SDK_PROTECTED):
            raise ValueError(f"Overlay touches a protected SDK path: {name}")
    return deletes, replaces, entries


def overlay_operations(vendor_types, deletes, replaces, entries, vendor_links=None):
    """Resolve only planned replacements; existing directory scaffolding stays intact."""
    if (deletes | replaces) - vendor_types.keys():
        raise ValueError("Overlay plan names a path absent from the vendor archive")
    if replaces - entries.keys():
        raise ValueError("Overlay is missing a planned replacement")
    if deletes & entries.keys():
        raise ValueError("Overlay provides a deleted path")
    if deletes & set(PARENTS):
        raise ValueError("Overlay cannot delete a required SDK parent directory")
    changed, vendor_links = deletes | replaces, vendor_links or {}
    if changed:
        for name, destination in vendor_links.items():
            if name in changed:
                continue
            seen = set()
            while True:
                if destination in changed:
                    raise ValueError(f"Overlay implicitly changes an unplanned vendor hardlink: {name}")
                if destination not in vendor_links:
                    break
                if destination in seen:
                    raise ValueError("Vendor hardlink cycle prevents overlay verification")
                seen.add(destination)
                destination = vendor_links[destination]
    operations = {}
    for name, path in entries.items():
        is_directory = stat.S_ISDIR(path.lstat().st_mode)
        if name in vendor_types and name not in replaces:
            if vendor_types[name] != tarfile.DIRTYPE or not is_directory:
                raise ValueError(f"Overlay overwrite is not in replace: {name}")
            continue
        if name in PARENTS and not is_directory:
            raise ValueError(f"Overlay SDK parent must remain a directory: {name}")
        operations[name] = path
    final_types = {name: kind for name, kind in vendor_types.items() if name not in deletes | replaces}
    for name, path in operations.items():
        final_types[name] = tarfile.DIRTYPE if stat.S_ISDIR(path.lstat().st_mode) else tarfile.REGTYPE
    for name in deletes | replaces | operations.keys():
        for parent in PurePosixPath(name).parents:
            parent_name = str(parent)
            # Omitting a complete archived subtree does not traverse it. Writes still
            # cannot use a deleted parent, and the following check refuses retained
            # descendants when a directory is deleted without all of its contents.
            if ((parent_name in deletes and name not in deletes)
                    or (parent_name in final_types and final_types[parent_name] != tarfile.DIRTYPE)):
                raise ValueError(f"Overlay traverses a deleted or non-directory parent: {parent_name}")
    # Exact deletions cannot leave descendants, and replacements cannot hide them behind a link/file.
    for name in deletes | operations.keys():
        if final_types.get(name) != tarfile.DIRTYPE:
            if any(child.startswith(name + "/") for child in final_types):
                raise ValueError(f"Overlay mutation leaves descendants under a non-directory: {name}")
    return operations


def overlay_metadata(info, preserve_ownership):
    """An installed overlay uses target-root numeric IDs; ordinary overlays remain root-owned."""
    mode = stat.S_IMODE(info.st_mode)
    if not preserve_ownership:
        return {"uid": 0, "gid": 0, "uname": "root", "gname": "root", "mode": mode & 0o777}
    if not stat.S_ISDIR(info.st_mode) and mode & 0o7000:
        raise ValueError("Installed overlay special permission bits are supported only on directories")
    # Empty names prevent tar from resolving a target numeric ID through a different host account name.
    return {"uid": info.st_uid, "gid": info.st_gid, "uname": "", "gname": "", "mode": mode}


def add_overlay(target, entries, epoch, preserve_ownership=False):
    for name, path in sorted(entries.items()):
        info = path.lstat()
        kind = (tarfile.DIRTYPE if stat.S_ISDIR(info.st_mode) else
                tarfile.SYMTYPE if stat.S_ISLNK(info.st_mode) else tarfile.REGTYPE)
        metadata = overlay_metadata(info, preserve_ownership)
        member = injected_member(name, epoch, kind, metadata["mode"])
        for key in ("uid", "gid", "uname", "gname"):
            setattr(member, key, metadata[key])
        if kind == tarfile.REGTYPE:
            member.size = info.st_size
            with path.open("rb") as content:
                target.addfile(member, content)
        else:
            if kind == tarfile.SYMTYPE:
                member.linkname = os.readlink(path)
            target.addfile(member)


def normalized_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe archive path: {name!r}")
    return str(path)


def digest_stream(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def check_vendor_member(member, parents_present, inject_sdk=True):
    name = normalized_name(member.name)
    if member.issparse() or any("sparse" in key.lower() for key in member.pax_headers):
        raise ValueError(f"Sparse entries require a separate preservation strategy: {name}")
    if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
        raise ValueError(f"Unsupported vendor entry: {name}")
    if inject_sdk and any(name == reserved or name.startswith(reserved + "/") for reserved in RESERVED):
        raise ValueError(f"Vendor archive already owns an injected path: {name}")
    if name in PARENTS or name == ".":
        if not member.isdir():
            raise ValueError(f"Injection parent must be a directory: {name}")
        parents_present.add(name)
    if member.islnk():
        normalized_name(member.linkname)
    # Vendor absolute symlinks are intentional on Linux. Nothing is extracted here.


def injected_member(name, epoch, kind=tarfile.DIRTYPE, mode=0o755):
    member = tarfile.TarInfo(name)
    member.type, member.mode, member.mtime = kind, mode, epoch
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    return member


def add_sdk(target, sdk_dir, epoch, profile_path=RESERVED[1]):
    target.addfile(injected_member("opt/dotnet", epoch))
    for parent, directories, files in os.walk(sdk_dir, followlinks=False):
        directories.sort()
        for name in sorted(directories + files):
            path = Path(parent) / name
            relative = path.relative_to(sdk_dir).as_posix()
            normalized_name(relative)
            if path.is_symlink():
                if Path(os.readlink(path)).is_absolute() or not path.resolve().is_relative_to(sdk_dir):
                    raise ValueError(f"SDK symlink escapes its directory: {relative}")
            member = target.gettarinfo(str(path), arcname="opt/dotnet/" + relative)
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f"Unsupported SDK entry: {relative}")
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
            member.mtime, member.mode = epoch, member.mode & 0o777
            if member.isfile():
                with path.open("rb") as content:
                    target.addfile(member, content)
            else:
                target.addfile(member)
    profile = injected_member(profile_path, epoch, tarfile.REGTYPE, 0o644)
    profile.size = len(PROFILE)
    target.addfile(profile, io.BytesIO(PROFILE))
    link = injected_member(RESERVED[2], epoch, tarfile.SYMTYPE, 0o777)
    link.linkname = "/opt/dotnet/dotnet"
    target.addfile(link)


def check_process(process, errors, label):
    if process.wait() != 0:
        errors.seek(0)
        detail = errors.read(4096).decode("utf-8", errors="replace").strip()
        raise ValueError(f"zstd {label} failed: {detail}")


def stop_process(process):
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            try:
                stream.close()
            except BrokenPipeError:
                pass
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def compose(rootfs, rootfs_sha256, sdk_dir, output, source_date_epoch=0, overlay_dir=None, overlay_plan=None,
            preserve_overlay_ownership=False, profile_path=RESERVED[1]):
    rootfs, sdk_dir, output = Path(rootfs), Path(sdk_dir).resolve() if sdk_dir is not None else None, Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output must not exist: {output}")
    if not output.parent.is_dir():
        raise ValueError("Output parent directory must already exist")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", rootfs_sha256):
        raise ValueError("A full rootfs SHA256 pin is required")
    if type(source_date_epoch) is not int or source_date_epoch < 0:
        raise ValueError("source-date-epoch must be a nonnegative integer")
    report = {}
    if sdk_dir is not None:
        report_path = sdk_dir / "sdk-payload-report.json"
        if report_path.is_symlink() or not report_path.is_file():
            raise ValueError("SDK directory requires a regular sdk-payload-report.json")
        report = json.loads(report_path.read_text())
        if report.get("schema_version") != 1 or report.get("rid") != "linux-arm64":
            raise ValueError("SDK report must describe schema 1, linux-arm64")
        if not (sdk_dir / "dotnet").is_file() or (sdk_dir / "dotnet").is_symlink():
            raise ValueError("SDK directory requires a regular dotnet host")
        expected_tree = report.get("tree_sha256")
        if not isinstance(expected_tree, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_tree):
            raise ValueError("SDK report requires a tree_sha256 fingerprint; stage the SDKs again")
        if tree_sha256(sdk_dir) != expected_tree:
            raise ValueError("SDK tree SHA256 mismatch; staged files changed after their report")
    deletes, replaces, overlay_entries = read_overlay(overlay_dir, overlay_plan)
    if preserve_overlay_ownership and overlay_dir is None:
        raise ValueError("preserve-overlay-ownership requires an explicitly staged overlay")
    for path in overlay_entries.values():
        overlay_metadata(path.lstat(), preserve_overlay_ownership)
    temporary = None
    try:
        with rootfs.open("rb") as source:
            if digest_stream(source) != rootfs_sha256.lower():
                raise ValueError("Vendor rootfs SHA256 mismatch")
            source.seek(0)
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".rootfs-compose-", delete=False) as dest:
                temporary = Path(dest.name)
                with tempfile.TemporaryFile() as decode_errors, tempfile.TemporaryFile() as encode_errors:
                    processes = []
                    try:
                        decode = subprocess.Popen(["timeout", "--kill-after=5s", "1800s", "zstd", "-q", "-d", "-c"], stdin=source,
                                                  stdout=subprocess.PIPE, stderr=decode_errors, start_new_session=True)
                        processes.append(decode)
                        encode = subprocess.Popen(["timeout", "--kill-after=5s", "1800s", "zstd", "-q", "-T1", "-3", "-c"], stdin=subprocess.PIPE,
                                                  stdout=dest, stderr=encode_errors, start_new_session=True)
                        processes.append(encode)
                        parents_present, vendor_types, vendor_links = set(), {}, {}
                        with tarfile.open(fileobj=decode.stdout, mode="r|") as vendor:
                            with tarfile.open(fileobj=encode.stdin, mode="w|", format=tarfile.PAX_FORMAT) as target:
                                for member in vendor:
                                    check_vendor_member(member, parents_present, sdk_dir is not None)
                                    name = normalized_name(member.name)
                                    if name in vendor_types:
                                        raise ValueError(f"Duplicate vendor entry: {name}")
                                    if sdk_dir is not None and name == profile_path:
                                        raise ValueError("Vendor owns the selected dotnet profile")
                                    vendor_types[name] = member.type
                                    if member.islnk():
                                        vendor_links[name] = normalized_name(member.linkname)
                                    if name in deletes | replaces:
                                        continue
                                    content = vendor.extractfile(member) if member.isfile() else None
                                    target.addfile(member, content)
                                    if content is not None:
                                        content.close()
                                # Drain padding so decompression errors cannot hide after the tar terminator.
                                with open(os.devnull, "wb") as sink:
                                    shutil.copyfileobj(decode.stdout, sink)
                                check_process(decode, decode_errors, "decompression")
                                operations = overlay_operations(vendor_types, deletes, replaces, overlay_entries, vendor_links)
                                for name in PARENTS:
                                    if name not in parents_present and name not in operations:
                                        target.addfile(injected_member(name, source_date_epoch))
                                add_overlay(target, operations, source_date_epoch, preserve_overlay_ownership)
                                if sdk_dir is not None:
                                    add_sdk(target, sdk_dir, source_date_epoch, profile_path)
                        encode.stdin.close()
                        check_process(encode, encode_errors, "compression")
                    finally:
                        for process in reversed(processes):
                            stop_process(process)
            with temporary.open("rb") as completed:
                output_sha256 = digest_stream(completed)
            # Atomic publication without overwriting even an empty existing file.
            os.link(temporary, output)
        result = {"rootfs_sha256": rootfs_sha256.lower(), "output_sha256": output_sha256,
                  "output_bytes": output.stat().st_size, "source_date_epoch": source_date_epoch,
                  "sdk_host_version": report.get("host_from_sdk")}
        if overlay_dir is not None:
            result["overlay"] = {"deleted_entries": len(deletes), "replaced_entries": len(replaces),
                                 "added_entries": len(operations) - len(replaces)}
            if preserve_overlay_ownership:
                result["overlay"]["ownership"] = "staged-numeric-ids"
        return result
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def compose_overlay(rootfs, rootfs_sha256, output, overlay_dir, overlay_plan,
                    source_date_epoch=0, preserve_overlay_ownership=False):
    """Layer explicit application mutations on a base while preserving its existing SDK."""
    return compose(rootfs, rootfs_sha256, None, output, source_date_epoch,
                   overlay_dir, overlay_plan, preserve_overlay_ownership)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--rootfs-sha256", required=True)
    parser.add_argument("--sdk-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-date-epoch", type=int, default=0)
    parser.add_argument("--overlay-dir", type=Path)
    parser.add_argument("--overlay-plan", type=Path)
    parser.add_argument("--preserve-overlay-ownership", action="store_true",
                        help="preserve prepared target-root numeric ownership and directory special mode bits")
    args = parser.parse_args()
    try:
        print(json.dumps(compose(args.rootfs, args.rootfs_sha256, args.sdk_dir,
                                 args.output, args.source_date_epoch, args.overlay_dir, args.overlay_plan,
                                 args.preserve_overlay_ownership), indent=2))
    except (OSError, ValueError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
