#!/usr/bin/env python3
"""Stream the actual preferred Linux source and public build inputs into a source-only release."""
from __future__ import annotations

import argparse
import functools
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile

PROJECT = Path(__file__).resolve().parents[2]
PROFILE = 'tch-g62h-imx91-rel3.3'
RESERVE = 500 * 1024**2
PREFIX = 'iwave-linux-source'
EXTERNAL = {'cryptodev.ko', 'igb_uio.ko', 'kpage_ncache.ko', 'mlan.ko', 'moal.ko'}
OMIT_PARTS = {'.git', '.local', '__pycache__', '.pytest_cache'}
OMIT_FILES = {'.env', '.netrc', '.git-credentials'}


def git(source, *arguments):
    result = subprocess.run(['git', '-C', str(source), *arguments], capture_output=True, timeout=60,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    if result.returncode:
        raise ValueError('Source Git inventory failed; no repository configuration is exported')
    return result.stdout


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')) or '\\' in name:
        raise ValueError('Unsafe source archive path')
    return name


def omitted(name):
    path = PurePosixPath(name)
    return bool(set(path.parts) & OMIT_PARTS) or path.name in OMIT_FILES


def record(root, name):
    name = relative(name)
    path = root / name
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('Source file traverses a symlink parent: ' + name)
    info = path.lstat()
    result = {'mode': stat.S_IMODE(info.st_mode), 'bytes': info.st_size}
    if stat.S_ISREG(info.st_mode):
        result.update(type='file', sha256=digest(path))
    elif stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        if Path(target).is_absolute() or not path.resolve().is_relative_to(root):
            raise ValueError('Source symlink escapes its selected tree: ' + name)
        result.update(type='symlink', target=target, sha256=hashlib.sha256(os.fsencode(target)).hexdigest())
    else:
        raise ValueError('Source inventory contains a special file or submodule: ' + name)
    return result


def source_inventory(source):
    if git(source, 'rev-parse', '--show-toplevel').decode().strip() != str(source):
        raise ValueError('Source must be the exact Git working-tree root')
    revision = git(source, 'rev-parse', '--verify', 'HEAD').decode().strip()
    if not re.fullmatch('[a-f0-9]{40,64}', revision):
        raise ValueError('Invalid source revision')
    raw = git(source, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
    names = sorted({relative(os.fsdecode(n)) for n in raw.split(b'\0') if n})
    added = {os.fsdecode(n) for n in git(source, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if n}
    files, excluded, deleted, added_hashes = {}, [], [], {}
    for name in names:
        if omitted(name):
            excluded.append(name)
            continue
        path = source / name
        if not path.exists() and not path.is_symlink():
            deleted.append(name)
            continue
        files[name] = record(source, name)
        if name in added:
            if files[name]['type'] != 'file':
                raise ValueError('Qualification additions require ordinary source files')
            added_hashes[name] = files[name]['sha256']
    diff = git(source, 'diff', '--no-ext-diff', '--binary', 'HEAD')
    return files, {'revision': revision, 'diff_sha256': hashlib.sha256(diff).hexdigest(),
        'added_files_sha256': added_hashes, 'deleted_tracked_files': deleted,
        'excluded_generated_or_private_paths': excluded}


def public_inventory(project):
    roots = ('components', 'profiles', 'tools', 'tests', 'docs', 'LICENSES')
    names = {'LICENSE', 'NOTICE', 'README.md', 'docs/source-delivery.md',
        'profiles/' + PROFILE + '/source-gaps.json'}
    for root in roots:
        directory = project / root
        if not directory.is_dir():
            raise ValueError('Public source package is missing required directory: ' + root)
        for parent, directories, files in os.walk(directory, followlinks=False):
            directories[:] = sorted(n for n in directories if not omitted((Path(parent) / n).relative_to(project).as_posix()))
            for name in directories + files:
                path = Path(parent) / name
                relative_path = path.relative_to(project).as_posix()
                if (path.is_symlink() or path.is_file()) and not omitted(relative_path):
                    names.add(relative_path)
    return {name: record(project, name) for name in sorted(names)}


def kernel_identity(project, kernel_set):
    manifest_path = kernel_set / 'kernel-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    policy = json.loads((project / 'profiles' / PROFILE / 'image-policy.json').read_bytes())
    if (digest(manifest_path) != policy['kernel_manifest_sha256']
            or manifest['release'] != policy['kernel_release']
            or manifest.get('static_qualification') != 'PASS_BUILD_AND_STATIC_ABI'):
        raise ValueError('Kernel identity differs from the accepted public image profile')
    config = record(kernel_set, 'config')
    if config['type'] != 'file' or any(config[key] != manifest['outputs']['config'][key] for key in ('bytes', 'sha256')):
        raise ValueError('Qualified kernel configuration differs from its manifest')
    external = manifest['stock_external_provenance']['files']
    if {PurePosixPath(name).name for name in external} != EXTERNAL:
        raise ValueError('Source gap must identify the exact five retained external modules')
    def declarations(table):
        result = {}
        for name, value in table.items():
            relative(name)
            if (type(value.get('bytes')) is not int or value['bytes'] < 0
                    or not isinstance(value.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', value['sha256'])):
                raise ValueError('Kernel source identity has an invalid file declaration')
            result[name] = {key: value[key] for key in ('bytes', 'sha256')}
        return result
    gaps = json.loads((project / 'profiles' / PROFILE / 'source-gaps.json').read_bytes())
    if {item.get('module') for item in gaps.get('external_modules', [])} != EXTERNAL:
        raise ValueError('Public source-gap notice must identify all five excluded modules')
    summary = {'schema': 'iwave.kernel-source-identities.v1', 'release': manifest['release'],
        'kernel_manifest_sha256': digest(manifest_path), 'outputs': declarations(manifest['outputs']),
        'modules': declarations(manifest['modules']), 'module_metadata': declarations(manifest['module_metadata']),
        'excluded_external_modules': {PurePosixPath(n).name: {'sha256': r['sha256']} for n, r in external.items()},
        'physical_acceptance': 'NOT_RUN', 'complete_corresponding_source_for_all_modules': False}
    return manifest, config, summary


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


class HashedReader:
    def __init__(self, stream):
        self.stream, self.hash = stream, hashlib.sha256()

    def read(self, size):
        data = self.stream.read(size)
        self.hash.update(data)
        return data


def add_file(target, archive_name, root, name, expected, epoch):
    path = root / name
    info = path.lstat()
    if (stat.S_IMODE(info.st_mode) != expected['mode'] or info.st_size != expected['bytes']
            or (expected['type'] == 'file' and not stat.S_ISREG(info.st_mode))
            or (expected['type'] == 'symlink' and (not stat.S_ISLNK(info.st_mode) or os.readlink(path) != expected['target']))):
        raise ValueError('Source changed before packaging: ' + archive_name)
    member = tarfile.TarInfo(archive_name)
    member.uid = member.gid = 0
    member.uname = member.gname = 'root'
    member.mode, member.mtime = expected['mode'], epoch
    if expected['type'] == 'symlink':
        member.type, member.linkname = tarfile.SYMTYPE, expected['target']
        target.addfile(member)
    else:
        member.size = expected['bytes']
        with path.open('rb') as stream:
            reader = HashedReader(stream)
            target.addfile(member, reader)
            if reader.hash.hexdigest() != expected['sha256'] or stream.read(1):
                raise ValueError('Source changed during packaging: ' + archive_name)


def add_json(target, name, document, epoch):
    content = (json.dumps(document, sort_keys=True, indent=2) + '\n').encode()
    member = tarfile.TarInfo(name)
    member.size, member.mode, member.mtime = len(content), 0o644, epoch
    member.uid = member.gid = 0
    member.uname = member.gname = 'root'
    target.addfile(member, io.BytesIO(content))


def package(source, project, kernel_set, output, max_output_bytes=1024**3, source_date_epoch=0, dry_run=False):
    source, project, kernel_set = (Path(p).resolve() for p in (source, project, kernel_set))
    output = Path(output)
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Source archive output must be absent with an existing parent')
    if type(max_output_bytes) is not int or max_output_bytes <= 0 or source_date_epoch < 0:
        raise ValueError('Use a positive compressed output bound and a nonnegative epoch')
    if shutil.disk_usage(output.parent).free < max_output_bytes + RESERVE:
        raise ValueError('Source archive needs its admitted output bound plus 500 MiB reserve')
    linux_files, identity = source_inventory(source)
    public_files = public_inventory(project)
    kernel, config, kernel_summary = kernel_identity(project, kernel_set)
    inputs = kernel['inputs']
    if (identity['revision'] != inputs['srcrev'] or identity['diff_sha256'] != inputs['source_diff_sha256']
            or identity['added_files_sha256'] != inputs['source_added_files_sha256']):
        raise ValueError('Actual Linux working tree differs from the qualified kernel source identity')
    manifest = {'schema': 'iwave.kernel-core-source-release.v1', 'profile': PROFILE,
        'scope': 'Actual preferred in-tree Linux sources, external ST IMU source and its patch/build recipe, public extensions and qualified config',
        'completeness': 'INCOMPLETE_FOR_FIVE_RETAINED_FACTORY_MODULES',
        'source_gap_reference': 'extensions/profiles/' + PROFILE + '/source-gaps.json',
        'excluded_external_modules': sorted(EXTERNAL), 'source': identity,
        'kernel_manifest_sha256': kernel_summary['kernel_manifest_sha256'],
        'kernel_release': kernel['release'], 'source_date_epoch': source_date_epoch,
        'files': {**{'linux/' + n: r for n, r in linux_files.items()},
            **{'extensions/' + n: r for n, r in public_files.items()}, 'qualification/kernel.config': config}}
    summary_bytes = (json.dumps(kernel_summary, sort_keys=True, indent=2) + '\n').encode()
    manifest['files']['qualification/kernel-identities.json'] = {'type': 'file', 'mode': 0o644,
        'bytes': len(summary_bytes), 'sha256': hashlib.sha256(summary_bytes).hexdigest()}
    if not linux_files or not (source / 'COPYING').is_file():
        raise ValueError('Linux source inventory must include its licensing notice')
    if dry_run:
        return {'status': 'VALIDATED_SOURCE_INPUTS_ONLY', 'source_revision': identity['revision'],
            'source_diff_sha256': identity['diff_sha256'], 'source_files': len(linux_files),
            'public_files': len(public_files), 'logical_input_bytes': sum(r['bytes'] for r in manifest['files'].values()),
            'max_output_bytes': max_output_bytes, 'reserve_bytes': RESERVE,
            'archive': 'NOT_CREATED', 'completeness': manifest['completeness']}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.kernel-source-', dir=output.parent, delete=False) as destination:
            temporary = Path(destination.name)
            with tempfile.TemporaryFile() as errors:
                child = subprocess.Popen(['timeout', '--kill-after=5s', '1800s', 'zstd', '-q', '-T1', '-3', '-c'],
                    stdin=subprocess.PIPE, stdout=destination, stderr=errors, start_new_session=True,
                    preexec_fn=functools.partial(resource.setrlimit, resource.RLIMIT_FSIZE, (max_output_bytes, max_output_bytes)))
                try:
                    with tarfile.open(fileobj=child.stdin, mode='w|', format=tarfile.PAX_FORMAT) as target:
                        for name, declaration in sorted(linux_files.items()):
                            add_file(target, PREFIX + '/linux/' + name, source, name, declaration, source_date_epoch)
                        for name, declaration in sorted(public_files.items()):
                            add_file(target, PREFIX + '/extensions/' + name, project, name, declaration, source_date_epoch)
                        add_file(target, PREFIX + '/qualification/kernel.config', kernel_set, 'config', config, source_date_epoch)
                        add_json(target, PREFIX + '/qualification/kernel-identities.json', kernel_summary, source_date_epoch)
                        add_json(target, PREFIX + '/source-manifest.json', manifest, source_date_epoch)
                    child.stdin.close()
                    if child.wait(timeout=30):
                        raise ValueError('Source archive compression failed or exceeded its output bound')
                finally:
                    try:
                        child.stdin.close()
                    except BrokenPipeError:
                        pass
                    stop(child)
        # Recheck the source graph after streaming; the archive must represent one coherent input.
        if source_inventory(source) != (linux_files, identity) or public_inventory(project) != public_files:
            raise ValueError('Source inputs changed while streaming the release archive')
        output_hash = digest(temporary)
        os.link(temporary, output)
        return {'status': 'PASS_SOURCE_ONLY_PACKAGE', 'archive_sha256': output_hash,
            'archive_bytes': output.stat().st_size, 'source_files': len(linux_files), 'public_files': len(public_files),
            'source_revision': identity['revision'], 'source_diff_sha256': identity['diff_sha256'],
            'completeness': manifest['completeness'], 'firmware_redistribution': 'BLOCKED'}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'kernel-set', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--project', type=Path, default=PROJECT)
    parser.add_argument('--max-output-bytes', type=int, default=1024**3)
    parser.add_argument('--source-date-epoch', type=int, default=0)
    parser.add_argument('--dry-run', action='store_true', help='Read-only input and storage validation, without an archive')
    args = parser.parse_args()
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        print(json.dumps(package(args.source, args.project, args.kernel_set, args.output,
            args.max_output_bytes, args.source_date_epoch, args.dry_run), indent=2))
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.TimeoutExpired) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Source packaging interrupted; owned incomplete output removed', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
