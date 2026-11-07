# SPDX-License-Identifier: GPL-2.0-only
"""Verify a published preferred-source snapshot without consulting Git metadata."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

SOURCE_REVISION = '02cf250c2e6bd375c11dae324ea145a39015d41b'
PROFILE = 'tch-g62h-imx91-rel3.3'
DEFAULT_RELEASE_LOCK = Path(__file__).resolve().parents[2] / 'profiles' / PROFILE / 'source-release.json'
SHA256 = re.compile('[a-f0-9]{64}')


def strict_document(path, limit):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise ValueError('Source identity JSON must be a bounded regular file')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Source identity JSON contains duplicate properties')
            result[key] = value
        return result
    def constant(_value):
        raise ValueError('Source identity JSON contains a non-JSON number')
    data = path.read_bytes()
    if len(data) != info.st_size:
        raise ValueError('Source identity JSON changed while reading')
    result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise ValueError('Source identity JSON must be an object')
    return result, hashlib.sha256(data).hexdigest(), len(data)


def relative_name(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('Unsafe source identity path')
    return name


def source_names(source):
    """Enumerate actual source entries without entering or reading a root .git path."""
    names, pending = set(), [source]
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                if parent == source and entry.name == '.git':
                    continue
                path = Path(entry.path)
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                    names.add(path.relative_to(source).as_posix())
                else:
                    raise ValueError('Source snapshot contains a special file')
    return names


def verify_entry(source, name, declaration):
    if (not isinstance(declaration, dict) or declaration.get('type') not in ('file', 'symlink')
            or type(declaration.get('mode')) is not int or not 0 <= declaration['mode'] <= 0o7777
            or type(declaration.get('bytes')) is not int or declaration['bytes'] < 0
            or not isinstance(declaration.get('sha256'), str) or not SHA256.fullmatch(declaration['sha256'])):
        raise ValueError('Invalid source file declaration: ' + name)
    path = source / name
    for parent in path.parents:
        if parent == source:
            break
        if parent.is_symlink():
            raise ValueError('Source snapshot traverses a symlink parent: ' + name)
    info = path.lstat()
    if stat.S_IMODE(info.st_mode) != declaration['mode'] or info.st_size != declaration['bytes']:
        raise ValueError('Source file mode or size differs from release: ' + name)
    if declaration['type'] == 'symlink':
        if not stat.S_ISLNK(info.st_mode):
            raise ValueError('Source link type differs from release: ' + name)
        target = os.readlink(path)
        if target != declaration.get('target') or Path(target).is_absolute():
            raise ValueError('Source link target differs from release: ' + name)
        try:
            contained = path.resolve().is_relative_to(source)
        except RuntimeError as error:
            raise ValueError('Source link contains a cycle: ' + name) from error
        if not contained:
            raise ValueError('Source link escapes its root: ' + name)
        actual_hash = hashlib.sha256(os.fsencode(target)).hexdigest()
    else:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Source file type differs from release: ' + name)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            actual_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Source file changed during verification: ' + name)
    if actual_hash != declaration['sha256']:
        raise ValueError('Source file bytes differ from release: ' + name)


def check_source_manifest(source, manifest, release_lock=DEFAULT_RELEASE_LOCK):
    """Bind every source entry to the explicitly selected, published manifest pin."""
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError('Source snapshot must be a directory')
    lock, _, _ = strict_document(release_lock, 1024 * 1024)
    if (lock.get('schema') != 'iwave.source-release.v1'
            or type(lock.get('source_manifest_bytes')) is not int or lock['source_manifest_bytes'] <= 0
            or not isinstance(lock.get('source_manifest_sha256'), str)
            or not SHA256.fullmatch(lock['source_manifest_sha256'])):
        raise ValueError('Public source-release lock requires exact source manifest size and SHA256')
    document, manifest_hash, manifest_bytes = strict_document(manifest, 64 * 1024**2)
    if manifest_hash != lock['source_manifest_sha256'] or manifest_bytes != lock['source_manifest_bytes']:
        raise ValueError('Source manifest differs from its published release pin')
    if document.get('schema') != 'iwave.kernel-core-source-release.v1' or document.get('profile') != PROFILE:
        raise ValueError('Wrong preferred-source manifest schema or hardware profile')
    identity = document.get('source')
    if (not isinstance(identity, dict) or identity.get('revision') != SOURCE_REVISION
            or not isinstance(identity.get('diff_sha256'), str) or not SHA256.fullmatch(identity['diff_sha256'])
            or not isinstance(identity.get('added_files_sha256'), dict)):
        raise ValueError('Wrong preferred-source revision or source fingerprint')
    table = document.get('files')
    if not isinstance(table, dict):
        raise ValueError('Source manifest must declare its files')
    files = {}
    for name, declaration in table.items():
        relative_name(name)
        if name.startswith('linux/'):
            relative = relative_name(name.removeprefix('linux/'))
            if relative == '.git' or relative.startswith('.git/'):
                raise ValueError('Source manifest cannot declare Git metadata')
            files[relative] = declaration
    if not files or 'COPYING' not in files:
        raise ValueError('Source manifest must include the Linux licensing notice')
    actual = source_names(source)
    if actual != files.keys():
        raise ValueError('Source inventory has missing or undeclared files')
    for name, declaration in files.items():
        verify_entry(source, name, declaration)
    for name, expected in identity['added_files_sha256'].items():
        relative_name(name)
        if name not in files or files[name].get('type') != 'file' or files[name].get('sha256') != expected:
            raise ValueError('Added-source identity differs from the released file inventory')
    if source_names(source) != actual:
        raise ValueError('Source inventory changed during verification')
    return {'srcrev': identity['revision'], 'source_diff_sha256': identity['diff_sha256'],
        'source_added_files_sha256': identity['added_files_sha256'],
        'source_manifest_sha256': manifest_hash, 'source_manifest_bytes': manifest_bytes}
