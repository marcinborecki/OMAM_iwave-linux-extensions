#!/usr/bin/env python3
"""Resolve and stage pinned Linux ARM64 SDKs; never modify a rootfs or device."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen


RID = "linux-arm64"
METADATA_BASE = "https://builds.dotnet.microsoft.com/dotnet/release-metadata"
CHUNK_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 16 * CHUNK_BYTES
MAX_MEMBERS = 200_000
MAX_ARCHIVE_BYTES = 8 * 1024**3
VERSION_PATTERN = r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*))?"


def version_key(version):
    match = re.fullmatch(VERSION_PATTERN, version) if isinstance(version, str) else None
    if not match:
        raise ValueError(f"Invalid SDK version: {version!r}")
    major, minor, patch, prerelease = match.groups()
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part)
                        for part in (prerelease or "").split("."))
    return (int(major), int(minor), int(patch), prerelease is None, identifiers)


def validate_url(value):
    if not isinstance(value, str):
        raise ValueError("Download and metadata URLs must be HTTPS URLs")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"Expected an HTTPS URL without credentials: {value!r}")


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int:
        raise ValueError("Expected a manifest with schema_version: 1")
    if manifest["schema_version"] != 1 or manifest.get("rid") != RID:
        raise ValueError("Only schema_version 1 and rid linux-arm64 are supported")
    sdks = manifest.get("sdks")
    if not isinstance(sdks, list) or not sdks or len(sdks) > 16:
        raise ValueError("Expected between 1 and 16 pinned SDKs")
    previous = None
    for sdk in sdks:
        if not isinstance(sdk, dict):
            raise ValueError("Each SDK must be an object")
        key = version_key(sdk.get("version"))
        if previous is not None and key <= previous:
            raise ValueError("SDK versions must be unique and ordered oldest to newest")
        previous = key
        for field in ("url", "metadata_url"):
            validate_url(sdk.get(field))
        digest = sdk.get("sha512")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{128}", digest):
            raise ValueError("Each SDK requires a full SHA512 digest")
    return sdks


def resolve(channel, version="latest"):
    if not re.fullmatch(r"\d+\.\d+", channel):
        raise ValueError("Channel must have the form 10.0 or 11.0")
    metadata_url = f"{METADATA_BASE}/{channel}/releases.json"
    with urlopen(metadata_url, timeout=60) as response:
        content = response.read(MAX_METADATA_BYTES + 1)
    if len(content) > MAX_METADATA_BYTES:
        raise ValueError("Release metadata exceeds size limit")
    metadata = json.loads(content)
    if metadata.get("channel-version") != channel:
        raise ValueError("Release metadata channel does not match the request")
    selected = metadata.get("latest-sdk") if version == "latest" else version
    version_key(selected)
    for release in metadata.get("releases", []):
        candidates = release.get("sdks", []) + [release.get("sdk", {})]
        for sdk in candidates:
            if sdk.get("version") != selected:
                continue
            for artifact in sdk.get("files", []):
                if (artifact.get("rid") == RID
                        and artifact.get("name", "").startswith("dotnet-sdk-")
                        and artifact.get("url", "").endswith(".tar.gz")):
                    pin = {"version": selected, "url": artifact["url"],
                           "sha512": artifact["hash"].lower(), "metadata_url": metadata_url}
                    validate_manifest({"schema_version": 1, "rid": RID, "sdks": [pin]})
                    return pin
    raise ValueError(f"SDK {selected!r} for {RID} not found in {metadata_url}")


def sha512_file(path):
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def cached_archive(sdk, cache_dir):
    archive = cache_dir / f"dotnet-sdk-{sdk['version']}-{RID}.tar.gz"
    expected = sdk["sha512"].lower()
    if archive.exists() or archive.is_symlink():
        if archive.is_symlink() or not archive.is_file() or sha512_file(archive) != expected:
            raise ValueError(f"SHA512 mismatch or invalid cache entry: {archive}")
        return archive
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".sdk-download-", delete=False) as stream:
            temporary = Path(stream.name)
            digest = hashlib.sha512()
            downloaded = 0
            with urlopen(sdk["url"], timeout=60) as response:
                validate_url(response.geturl())
                while chunk := response.read(CHUNK_BYTES):
                    downloaded += len(chunk)
                    if downloaded > MAX_ARCHIVE_BYTES:
                        raise ValueError("Compressed SDK archive exceeds size limit")
                    digest.update(chunk)
                    stream.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f"SHA512 mismatch for SDK {sdk['version']}")
        # A hard link publishes a complete file without replacing another writer's entry.
        try:
            os.link(temporary, archive)
        except FileExistsError:
            if archive.is_symlink() or not archive.is_file() or sha512_file(archive) != expected:
                raise ValueError(f"Conflicting cache entry: {archive}") from None
        return archive
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def safe_member(member, destination):
    name = PurePosixPath(member.name)
    if name.is_absolute() or ".." in name.parts or "\\" in member.name:
        raise ValueError(f"Unsafe archive path: {member.name!r}")
    if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
        raise ValueError(f"Unsupported archive entry: {member.name!r}")
    if str(name) == "." and not member.isdir():
        raise ValueError("Archive root entry must be a directory")
    root = Path(destination).resolve()
    target = root / str(name)
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Archive path escapes destination: {member.name!r}")
    if member.issym() or member.islnk():
        link = PurePosixPath(member.linkname)
        if link.is_absolute() or "\\" in member.linkname:
            raise ValueError(f"Unsafe archive link: {member.linkname!r}")
        base = target.parent if member.issym() else root
        if not (base / str(link)).resolve().is_relative_to(root):
            raise ValueError(f"Archive link escapes destination: {member.linkname!r}")
    if hasattr(tarfile, "data_filter"):
        return tarfile.data_filter(member, destination)
    # Older patched Python versions may lack extraction filters entirely.
    filtered = copy.copy(member)
    filtered.uid, filtered.gid = os.getuid(), os.getgid()
    filtered.uname = filtered.gname = ""
    filtered.mode &= 0o755
    return filtered


def extract_archive(archive, destination):
    members, logical_bytes = [], 0
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            safe_member(member, destination)
            members.append(member)
            if member.isfile():
                logical_bytes += member.size
            if len(members) > MAX_MEMBERS or logical_bytes > MAX_ARCHIVE_BYTES:
                raise ValueError("Unpacked SDK archive exceeds size or entry limit")
        if not any(str(PurePosixPath(member.name)) == "dotnet" and member.isfile()
                   for member in members):
            raise ValueError("Each SDK archive must contain its regular dotnet host file")
        if hasattr(tarfile, "data_filter"):
            bundle.extractall(destination, members=members, filter=safe_member)
        else:
            for member in members:
                bundle.extract(safe_member(member, destination), destination)
    return {"compressed_bytes": archive.stat().st_size,
            "archive_logical_bytes": logical_bytes, "archive_entries": len(members)}


def tree_sizes(root):
    logical_bytes = allocated_bytes = file_count = directory_count = 0
    for path in root.rglob("*"):
        info = path.lstat()
        allocated_bytes += info.st_blocks * 512
        if path.is_file() and not path.is_symlink():
            logical_bytes += info.st_size
            file_count += 1
        elif path.is_dir() and not path.is_symlink():
            directory_count += 1
    return {"logical_bytes": logical_bytes, "allocated_bytes_on_host": allocated_bytes,
            "file_count": file_count, "directory_count": directory_count}


def tree_sha256(root):
    """Fingerprint relative entries and bytes, excluding only the root report itself."""
    root, entries = Path(root), []
    def walk_error(error):
        raise error
    for parent, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in directories + files:
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            if relative != "sdk-payload-report.json":
                entries.append((relative, path))
    digest = hashlib.sha256()
    for relative, path in sorted(entries):
        mode = path.lstat().st_mode
        record = {"path": relative, "mode": stat.S_IMODE(mode)}
        if stat.S_ISREG(mode):
            content_hash = hashlib.sha256()
            with path.open("rb") as content:
                while chunk := content.read(CHUNK_BYTES):
                    content_hash.update(chunk)
            record.update(type="file", sha256=content_hash.hexdigest())
        elif stat.S_ISDIR(mode):
            record.update(type="directory")
        elif stat.S_ISLNK(mode):
            record.update(type="symlink", target=os.readlink(path))
        else:
            raise ValueError(f"Unsupported SDK entry: {relative}")
        digest.update((json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"))
    return digest.hexdigest()


def stage(manifest, cache_dir, output_dir):
    sdks = validate_manifest(manifest)
    cache_dir, output_dir = Path(cache_dir), Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"Output directory must not exist: {output_dir}")
    if not output_dir.parent.is_dir():
        raise ValueError("Output parent directory must already exist")
    # Verify every compressed archive before creating or extracting the payload.
    archives = [cached_archive(sdk, cache_dir) for sdk in sdks]
    with tempfile.TemporaryDirectory(dir=output_dir.parent, prefix=".sdk-stage-") as temporary:
        staging = Path(temporary)
        reports = []
        for sdk, archive in zip(sdks, archives):
            reports.append({**sdk, **extract_archive(archive, staging)})
            if not (staging / "sdk" / sdk["version"]).is_dir():
                raise ValueError(f"Archive is missing SDK directory {sdk['version']}")
        if not (staging / "dotnet").is_file() or (staging / "dotnet").is_symlink():
            raise ValueError("SDK payload is missing its regular dotnet host file")
        report = {"schema_version": 1, "rid": RID, "sdks": reports,
                  "host_from_sdk": sdks[-1]["version"], "payload": tree_sizes(staging),
                  "tree_sha256": tree_sha256(staging)}
        (staging / "sdk-payload-report.json").write_text(json.dumps(report, indent=2) + "\n")
        # Exclusive creation preserves an existing destination, including an empty one.
        output_dir.mkdir(mode=0o755)
        try:
            for child in staging.iterdir():
                child.rename(output_dir / child.name)
        except BaseException:
            shutil.rmtree(output_dir)
            raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolver = commands.add_parser("resolve", help="Resolve an official SDK release to a pinned JSON entry")
    resolver.add_argument("--channel", required=True)
    resolver.add_argument("--version", default="latest")
    resolver.add_argument("--output", type=Path, required=True)
    stager = commands.add_parser("stage", help="Stage pinned SDKs in a new host-side directory")
    stager.add_argument("--manifest", type=Path, required=True)
    stager.add_argument("--cache-dir", type=Path, required=True)
    stager.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "resolve":
            result = resolve(args.channel, args.version)
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2)
                stream.write("\n")
        else:
            result = stage(json.loads(args.manifest.read_text()), args.cache_dir, args.output_dir)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
