#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Pack a GPL kernel core, or restore its five exact factory modules from a supplied BSP."""
from __future__ import annotations

import argparse
import functools
import hashlib
import io
import json
import os
from pathlib import Path
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
sys.path.insert(0, str(PROJECT / 'tools/image'))
from build_base import source_identities
from image_io import digest
from qualified_inputs import (QUALIFIED, STOCK_MODULES, checked_file, document,
    regular_files, relative_name, validate_kernel, validate_vendor)

PROFILE = 'tch-g62h-imx91-rel3.3'
PREFIX = 'iwave-kernel-core'
MAX_OUTPUT = 128 * 1024**2
RESERVE = 500 * 1024**2
PUBLIC_REPOSITORY = 'https://github.com/marcinborecki/OMAM_iwave-linux-extensions'
COUNTS = {'native_modules': 807, 'stock_modules': 5, 'imu_modules': 2, 'total_modules': 814}
NOTICE = b'''GPL kernel-core binary delivery for the iWave TCH G62H i.MX91 REL3.3 profile.
This bundle contains Image, DTB, 807 native modules and two upstream ST IMU modules,
plus exact qualification metadata. The five factory external modules are omitted.
The corresponding preferred Linux source archive and current public tools are
identified in SOURCE.json. Original Linux/NXP/iWave/ST and other contributor
notices and per-file licensing remain with those preferred sources. GPLv2 and the
Linux syscall exception text are included. This bundle contains no rootfs, recovery
firmware, bootloader, SDK, OMAM application or private installation profile.
Restoring the omitted modules from an exact user-supplied BSP permits local full-set
validation; it does not claim complete-image source or separate blob clearance.
Physical boot, CAN, IMU and power/wake acceptance are not established by packaging.
'''


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def declaration(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def output_path(path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError('Use an absent output path with an existing parent')
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Output cannot traverse a symlink')
    return path


def safe_inputs(inputs):
    """Retain only the source/build identity contract, never receipt command or host data."""
    if not isinstance(inputs, dict):
        raise ValueError('Kernel inputs must be an identity object')
    for key, value in inputs.items():
        if key == 'source_added_files_sha256':
            if not isinstance(value, dict):
                raise ValueError('Invalid added-source identities')
            for name, sha in value.items():
                relative_name(name)
                if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
                    raise ValueError('Invalid added-source hash')
        elif key == 'srcrev':
            if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{40}', value):
                raise ValueError('Invalid source revision')
        elif key == 'kernel_release':
            if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9_.+-]{1,128}', value):
                raise ValueError('Invalid kernel release')
        elif key == 'source_manifest_bytes':
            if type(value) is not int or value <= 0:
                raise ValueError('Invalid source manifest size')
        elif key.endswith('_sha256'):
            if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
                raise ValueError('Build input is not a bounded SHA256 identity')
        else:
            raise ValueError('Kernel input is not part of the public identity contract: ' + key)
    return inputs


def source_reference(manifest, manifest_hash):
    profile = PROJECT / 'profiles' / PROFILE
    release = document(profile / 'source-release.json', 'Public source release')
    inputs = safe_inputs(manifest['inputs'])
    if 'source_manifest_sha256' in inputs:
        if (inputs['source_manifest_sha256'] != release.get('source_manifest_sha256')
                or inputs.get('source_manifest_bytes') != release.get('source_manifest_bytes')):
            raise ValueError('Built kernel differs from the published preferred-source manifest')
    else:
        maintained = document(profile / 'image-policy.json', 'Maintained kernel association')
        if manifest_hash != maintained.get('kernel_manifest_sha256'):
            raise ValueError('Legacy kernel needs the exact maintained source association')
    archive = release['archive']
    if (not isinstance(archive.get('url'), str) or not archive['url'].startswith('https://')
            or not re.fullmatch('[a-f0-9]{64}', archive.get('sha256', ''))):
        raise ValueError('Public preferred-source archive reference is incomplete')
    return {'archive': {k: archive[k] for k in ('name', 'url', 'bytes', 'sha256')},
        'source_manifest_sha256': release['source_manifest_sha256'],
        'source_manifest_bytes': release['source_manifest_bytes'],
        'public_tools_repository': PUBLIC_REPOSITORY,
        'bundle_tool_sha256': digest(Path(__file__)),
        'scope': 'Preferred Linux core and ST IMU sources; the five factory binaries are not delivered'}


def public_kernel_metadata(manifest):
    """The preserved manifest is a typed identity document, not a build-log container."""
    allowed = {'schema', 'release', 'inputs', 'outputs', 'module_vermagic', 'module_layout_bytes',
        'module_metadata', 'modules', 'stock_external_provenance', 'physical_loading', 'static_qualification'}
    if set(manifest) - allowed:
        raise ValueError('Kernel manifest contains non-public identity fields')
    safe_inputs(manifest['inputs'])
    for key in ('outputs', 'module_metadata', 'modules'):
        allowed_record = {'bytes', 'sha256', 'versioned_imports'} if key == 'modules' else {'bytes', 'sha256'}
        for name, item in manifest[key].items():
            relative_name(name)
            if set(item) - allowed_record:
                raise ValueError('Kernel file declaration contains non-public identity fields')
    stock = manifest['stock_external_provenance']
    if set(stock) != {'rootfs_sha256', 'files'}:
        raise ValueError('Kernel stock provenance contains non-public identity fields')
    for name, item in stock['files'].items():
        relative_name(name)
        if set(item) - {'bytes', 'sha256', 'member', 'retained_path'}:
            raise ValueError('Stock module provenance contains non-public identity fields')


def no_embedded_blobs(root):
    config = (Path(root) / 'config').read_text()
    for name in ('CONFIG_INITRAMFS_SOURCE', 'CONFIG_EXTRA_FIRMWARE'):
        entries = [line for line in config.splitlines() if line.startswith(name + '=')]
        if entries != [name + '=""']:
            raise ValueError('Kernel core must have an explicitly empty ' + name)


def minimal_receipt(receipt, kernel):
    manifest = kernel['manifest']
    return {'schema': receipt['schema'], 'status': QUALIFIED,
        'inputs': safe_inputs(manifest['inputs']), 'config_sha256': manifest['outputs']['config']['sha256'],
        'qualification': {'status': QUALIFIED, **COUNTS, 'kernel_manifest': {'sha256': kernel['manifest_sha256']},
            'outputs': manifest['outputs']}, 'physical_loading': 'NOT_RUN', 'hardware_readback': 'NOT_RUN'}


def excluded_modules(manifest):
    stock = manifest.get('stock_external_provenance', {}).get('files', {})
    if not isinstance(stock, dict) or len(stock) != 5 or {Path(n).name for n in stock} != STOCK_MODULES:
        raise ValueError('Bundle must omit exactly the five stock external modules')
    result = {}
    for name, item in stock.items():
        relative_name(name)
        for key in ('member', 'retained_path'):
            if key in item:
                relative_name(item[key])
        record = manifest['modules'][name]
        result[name] = {key: record[key] for key in ('bytes', 'sha256')}
    return result


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


class HashReader:
    def __init__(self, stream):
        self.stream, self.hash = stream, hashlib.sha256()

    def read(self, size):
        data = self.stream.read(size)
        self.hash.update(data)
        return data


def member(name, size):
    info = tarfile.TarInfo(PREFIX + '/' + name)
    info.size, info.mode, info.mtime = size, 0o644, 0
    info.uid = info.gid = 0
    info.uname = info.gname = 'root'
    return info


def pack(kernel_root, kernel_receipt, vendor_root, vendor_manifest, output, record=None,
         max_output_bytes=MAX_OUTPUT):
    kernel_root, kernel_receipt = Path(kernel_root), Path(kernel_receipt)
    output = output_path(output)
    record_path = output_path(record) if record is not None else None
    if record_path == output:
        raise ValueError('Archive and release record require different paths')
    if type(max_output_bytes) is not int or not 0 < max_output_bytes <= MAX_OUTPUT:
        raise ValueError('Kernel-core output bound must be positive and at most 128 MiB')
    if shutil.disk_usage(output.parent).free < max_output_bytes + RESERVE:
        raise ValueError('Kernel-core archive needs its output bound plus 500 MiB reserve')
    vendor = validate_vendor(vendor_root, vendor_manifest)
    kernel = validate_kernel(kernel_root, kernel_receipt, vendor, source_identities())
    manifest = kernel['manifest']
    public_kernel_metadata(manifest)
    no_embedded_blobs(kernel_root)
    missing = excluded_modules(manifest)
    sources = source_reference(manifest, kernel['manifest_sha256'])
    original = document(kernel_receipt, 'Completed kernel receipt')
    receipt_bytes = json_bytes(minimal_receipt(original, kernel))
    payloads = {'kernel-build.json': receipt_bytes, 'NOTICE': NOTICE,
        'LICENSE': (PROJECT / 'LICENSES/GPL-2.0-only.txt').read_bytes(),
        'LICENSES/Linux-syscall-note.txt': (PROJECT / 'LICENSES/Linux-syscall-note.txt').read_bytes(),
        'SOURCE.json': json_bytes(sources)}
    tables = {**manifest['outputs'], **manifest['modules'], **manifest['module_metadata']}
    tables['kernel-manifest.json'] = {'bytes': (kernel_root / 'kernel-manifest.json').stat().st_size,
        'sha256': kernel['manifest_sha256']}
    delivered = {name: item for name, item in tables.items() if name not in missing}
    files = {'kernel/' + name: {k: item[k] for k in ('bytes', 'sha256')} for name, item in delivered.items()}
    files.update({name: declaration(data) for name, data in payloads.items()})
    delivery = {'schema': 'iwave.kernel-core-delivery.v1', 'profile': PROFILE,
        'kernel_release': manifest['release'], 'scope': 'GPL_KERNEL_CORE_ONLY',
        'delivered_modules': 809, 'restored_modules': 814,
        'missing_factory_modules': missing, 'files': files,
        'vendor_manifest_sha256': vendor['manifest_sha256'],
        'stock_rootfs_sha256': vendor['files']['Linux/rootfs.tar.zst']['sha256'],
        'kernel_manifest_sha256': kernel['manifest_sha256'], 'source': sources,
        'physical_acceptance': 'NOT_RUN'}
    delivery_bytes = json_bytes(delivery)
    temporary = record_temporary = None
    accepted = False
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.kernel-core-', delete=False) as destination:
            temporary = Path(destination.name)
            os.fchmod(destination.fileno(), 0o644)
            with tempfile.TemporaryFile() as errors:
                child = subprocess.Popen(['timeout', '--kill-after=5s', '300s', 'zstd', '-q', '-T1', '-3', '-c'],
                    stdin=subprocess.PIPE, stdout=destination, stderr=errors, start_new_session=True,
                    preexec_fn=functools.partial(resource.setrlimit, resource.RLIMIT_FSIZE, (max_output_bytes, max_output_bytes)))
                try:
                    with tarfile.open(fileobj=child.stdin, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                        for name, item in sorted(delivered.items()):
                            path = checked_file(kernel_root, name, item)
                            with path.open('rb') as stream:
                                reader = HashReader(stream)
                                archive.addfile(member('kernel/' + name, item['bytes']), reader)
                                if reader.hash.hexdigest() != item['sha256'] or stream.read(1):
                                    raise ValueError('Qualified kernel changed while packaging')
                        for name, data in sorted({**payloads, 'delivery.json': delivery_bytes}.items()):
                            archive.addfile(member(name, len(data)), io.BytesIO(data))
                    child.stdin.close()
                    if child.wait(timeout=30):
                        raise ValueError('Kernel-core compression failed or exceeded its output bound')
                finally:
                    try:
                        child.stdin.close()
                    except BrokenPipeError:
                        pass
                    stop(child)
        release = {'schema': 'iwave.kernel-core-release.v1', 'profile': PROFILE,
            'delivery_sha256': hashlib.sha256(delivery_bytes).hexdigest(),
            'archive': {'name': output.name, 'bytes': temporary.stat().st_size, 'sha256': digest(temporary)},
            'delivered_modules': 809, 'restored_modules': 814, 'delivered_files': len(files),
            'scope': 'GPL_KERNEL_CORE_ONLY', 'source': sources}
        os.link(temporary, output)
        if record_path is not None:
            with tempfile.NamedTemporaryFile(dir=record_path.parent, prefix='.kernel-core-record-', delete=False) as stream:
                record_temporary = Path(stream.name)
                stream.write(json_bytes(release))
                os.fchmod(stream.fileno(), 0o644)
            os.link(record_temporary, record_path)
        accepted = True
        return release
    finally:
        for path, scratch in ((output, temporary), (record_path, record_temporary)):
            if scratch is not None:
                if not accepted and path is not None and (path.exists() or path.is_symlink()):
                    current, owned = path.lstat(), scratch.lstat()
                    if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        path.unlink()
                scratch.unlink(missing_ok=True)


def restore(bundle_root, baseline, vendor_root, release_record=None):
    bundle_root, baseline = Path(bundle_root).absolute(), Path(baseline).absolute()
    if bundle_root.is_symlink() or any(p.is_symlink() for p in bundle_root.parents):
        raise ValueError('Bundle root cannot traverse a symlink')
    release_record = Path(release_record) if release_record is not None else PROJECT / 'profiles' / PROFILE / 'kernel-core-release.json'
    trusted = document(release_record, 'Trusted kernel-core release record')
    delivery_path = bundle_root / 'delivery.json'
    delivery = document(delivery_path, 'Kernel-core delivery')
    if trusted.get('schema') != 'iwave.kernel-core-release.v1' or digest(delivery_path) != trusted.get('delivery_sha256'):
        raise ValueError('Kernel-core delivery differs from its trusted release record')
    if (delivery.get('schema') != 'iwave.kernel-core-delivery.v1' or delivery.get('profile') != PROFILE
            or delivery.get('delivered_modules') != 809 or delivery.get('restored_modules') != 814):
        raise ValueError('Wrong kernel-core delivery profile or module scope')
    files = delivery.get('files')
    if not isinstance(files, dict) or 'delivery.json' in files:
        raise ValueError('Kernel-core delivery must declare its payload files')
    for name, item in files.items():
        relative_name(name)
        checked_file(bundle_root, name, item)
    kernel_root = bundle_root / 'kernel'
    manifest = document(kernel_root / 'kernel-manifest.json', 'Delivered kernel manifest')
    public_kernel_metadata(manifest)
    missing = excluded_modules(manifest)
    if missing != delivery.get('missing_factory_modules'):
        raise ValueError('Delivered kernel omitted a different factory module set')
    required_kernel = {'kernel/' + name for name in {"kernel-manifest.json", *manifest['outputs'], *manifest['modules'], *manifest['module_metadata']} if name not in missing}
    if {name for name in files if name.startswith('kernel/')} != required_kernel:
        raise ValueError('Delivered kernel file inventory differs from its qualified manifest')
    actual = regular_files(bundle_root)
    stock_names = {'kernel/' + name for name in missing}
    present = actual & stock_names
    if present and present != stock_names:
        raise ValueError('Bundle contains a partial factory-module restoration')
    if actual != set(files) | {'delivery.json'} | present:
        raise ValueError('Bundle contains extra or missing payload')
    for name in present:
        checked_file(bundle_root, name, missing[name.removeprefix('kernel/')])
    if digest(kernel_root / 'kernel-manifest.json') != delivery.get('kernel_manifest_sha256'):
        raise ValueError('Delivered kernel manifest hash differs')
    source_reference(manifest, delivery['kernel_manifest_sha256'])
    no_embedded_blobs(kernel_root)
    vendor = validate_vendor(vendor_root, baseline / 'inputs/manifest.json')
    if (vendor['manifest_sha256'] != delivery.get('vendor_manifest_sha256')
            or vendor['files']['Linux/rootfs.tar.zst']['sha256'] != delivery.get('stock_rootfs_sha256')):
        raise ValueError('Supplied BSP differs from this kernel-core build')
    stock = document(baseline / 'baseline/stock-external-modules.json', 'Prepared stock modules')
    if stock.get('rootfs_sha256') != delivery['stock_rootfs_sha256'] or set(stock.get('files', {})) != STOCK_MODULES:
        raise ValueError('Prepared baseline does not bind the exact five factory modules')
    sources = {}
    for target, expected in missing.items():
        original = stock['files'][Path(target).name]
        if any(original.get(key) != expected[key] for key in ('bytes', 'sha256')):
            raise ValueError('Factory module identity differs from delivered provenance')
        name = relative_name(original['retained_path'])
        checked_file(baseline, name, expected)
        sources[target] = name
    created, directories = [], []
    try:
        if not present:
            for name, source_name in sources.items():
                target = kernel_root / name
                pending, parent = [], target.parent
                while not parent.exists():
                    pending.append(parent)
                    parent = parent.parent
                if any(p.is_symlink() for p in target.parents):
                    raise ValueError('Factory restoration cannot traverse a symlink')
                for directory in reversed(pending):
                    directory.mkdir(mode=0o755)
                    directories.append(directory)
                path = checked_file(baseline, source_name, missing[name])
                with target.open('xb') as destination:
                    info = os.fstat(destination.fileno())
                    created.append((target, info.st_dev, info.st_ino))
                    with path.open('rb') as incoming:
                        shutil.copyfileobj(incoming, destination, 1024 * 1024)
                    os.fchmod(destination.fileno(), 0o644)
                checked_file(kernel_root, name, missing[name])
        result = validate_kernel(kernel_root, bundle_root / 'kernel-build.json', vendor, source_identities())
        return {'status': 'PASS_RESTORED_COMPLETE_KERNEL', 'kernel_root': str(kernel_root),
            'kernel_receipt': str(bundle_root / 'kernel-build.json'), 'counts': result['counts'],
            'restored_files': len(created), 'idempotent': bool(present), 'physical_acceptance': 'NOT_RUN'}
    except BaseException:
        for path, device, inode in reversed(created):
            if path.exists() and (path.stat().st_dev, path.stat().st_ino) == (device, inode):
                path.unlink()
        for directory in reversed(directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    packer = commands.add_parser('pack')
    for name in ('kernel-root', 'kernel-receipt', 'vendor-root', 'vendor-manifest', 'output'):
        packer.add_argument('--' + name, type=Path, required=True)
    packer.add_argument('--record', type=Path)
    packer.add_argument('--max-output-bytes', type=int, default=MAX_OUTPUT)
    restorer = commands.add_parser('restore')
    for name in ('bundle-root', 'baseline', 'vendor-root'):
        restorer.add_argument('--' + name, type=Path, required=True)
    restorer.add_argument('--release-record', type=Path)
    args = parser.parse_args()
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.command == 'pack':
            result = pack(args.kernel_root, args.kernel_receipt, args.vendor_root, args.vendor_manifest,
                args.output, args.record, args.max_output_bytes)
        else:
            result = restore(args.bundle_root, args.baseline, args.vendor_root, args.release_record)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.TimeoutExpired) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Kernel-core operation interrupted; owned partial work removed', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
