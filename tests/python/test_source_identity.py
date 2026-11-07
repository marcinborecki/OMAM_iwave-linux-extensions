# SPDX-License-Identifier: GPL-2.0-only
"""Preferred-source archive verification and compact baseline boundaries on tiny real files."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/kernel'))
import build
from qualify import native_baseline_names
from source_identity import SOURCE_REVISION, check_source_manifest, strict_document


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def save_json(path, data):
    return save(path, (json.dumps(data, sort_keys=True) + '\n').encode())


@pytest.fixture
def snapshot(tmp_path):
    source = tmp_path / 'linux'
    source.mkdir()
    inputs = {'COPYING': b'fixture licensing notice\n', '.gitignore': b'*.o\n',
        'drivers/driver.c': b'actual preferred source\n', 'scripts/build.sh': b'#!/bin/sh\n'}
    records = {}
    for name, data in inputs.items():
        sha = save(source / name, data)
        mode = 0o755 if name.endswith('.sh') else 0o644
        (source / name).chmod(mode)
        records['linux/' + name] = {'type': 'file', 'bytes': len(data), 'mode': mode, 'sha256': sha}
    (source / 'drivers/link.c').symlink_to('driver.c')
    target = b'driver.c'
    records['linux/drivers/link.c'] = {'type': 'symlink', 'bytes': len(target), 'mode': 0o777,
        'target': target.decode(), 'sha256': hashlib.sha256(target).hexdigest()}
    manifest, lock = tmp_path / 'source-manifest.json', tmp_path / 'source-release.json'
    document = {'schema': 'iwave.kernel-core-source-release.v1', 'profile': 'tch-g62h-imx91-rel3.3',
        'source': {'revision': SOURCE_REVISION, 'diff_sha256': 'a' * 64,
            'added_files_sha256': {'drivers/driver.c': records['linux/drivers/driver.c']['sha256']}},
        'files': records}
    def repin():
        sha = save_json(manifest, document)
        save_json(lock, {'schema': 'iwave.source-release.v1', 'source_manifest_sha256': sha,
            'source_manifest_bytes': manifest.stat().st_size})
    repin()
    return SimpleNamespace(source=source, manifest=manifest, lock=lock, document=document, repin=repin)


def check(snapshot):
    return check_source_manifest(snapshot.source, snapshot.manifest, snapshot.lock)


def test_extracted_source_accepts_exact_files_modes_links_and_published_identity_without_git(snapshot):
    result = check(snapshot)
    assert result['srcrev'] == SOURCE_REVISION
    assert result['source_diff_sha256'] == 'a' * 64
    assert result['source_added_files_sha256'] == snapshot.document['source']['added_files_sha256']
    assert result['source_manifest_sha256'] == hashlib.sha256(snapshot.manifest.read_bytes()).hexdigest()
    assert result['source_manifest_bytes'] == snapshot.manifest.stat().st_size


def test_git_metadata_is_opaque_and_never_walked_or_read(snapshot, monkeypatch):
    directory = snapshot.source / '.git'
    directory.mkdir()
    save(directory / 'unread-metadata', b'not part of source verification')
    original_scandir, original_open = os.scandir, Path.open
    def scandir(path):
        if Path(path) == directory or directory in Path(path).parents:
            pytest.fail('Source snapshot verification must not enter .git')
        return original_scandir(path)
    def open_path(path, *args, **kwargs):
        if path == directory or directory in path.parents:
            pytest.fail('Source snapshot verification must not read .git')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(os, 'scandir', scandir)
    monkeypatch.setattr(Path, 'open', open_path)
    monkeypatch.setattr(build.subprocess, 'run', lambda *a, **k: pytest.fail('No Git or other subprocess belongs to snapshot verification'))
    assert check(snapshot)['srcrev'] == SOURCE_REVISION


@pytest.mark.parametrize('mutation', ['bytes', 'mode', 'missing', 'extra-source', 'ignored-extra'])
def test_changed_or_undeclared_relevant_source_is_refused(snapshot, mutation):
    path = snapshot.source / 'drivers/driver.c'
    if mutation == 'bytes':
        path.write_bytes(b'changed preferred data\n')
    elif mutation == 'mode':
        path.chmod(0o755)
    elif mutation == 'missing':
        path.unlink()
    elif mutation == 'extra-source':
        save(snapshot.source / 'drivers/injected.c', b'unreviewed compilation input')
    else:
        save(snapshot.source / 'injected.o', b'ignored by Git still cannot enter this source snapshot')
    with pytest.raises(ValueError):
        check(snapshot)


def test_same_size_source_tampering_is_caught_by_actual_hash(snapshot):
    path = snapshot.source / 'drivers/driver.c'
    path.write_bytes(b'X' * path.stat().st_size)
    with pytest.raises(ValueError, match='bytes differ'):
        check(snapshot)


@pytest.mark.parametrize('field,value', [('source_manifest_sha256', '0' * 64), ('source_manifest_bytes', 1)])
def test_manifest_must_match_the_independent_release_lock(snapshot, field, value):
    data = json.loads(snapshot.lock.read_text())
    data[field] = value
    save_json(snapshot.lock, data)
    with pytest.raises(ValueError, match='published release pin'):
        check(snapshot)


def test_unsafe_source_path_is_refused_even_if_manifest_hash_is_repinned(snapshot):
    snapshot.document['files']['linux/../outside.c'] = snapshot.document['files']['linux/COPYING']
    snapshot.repin()
    with pytest.raises(ValueError, match='Unsafe'):
        check(snapshot)


def test_declared_source_link_must_stay_inside_the_selected_source_root(snapshot):
    path = snapshot.source / 'drivers/link.c'
    path.unlink()
    target = '../../outside.c'
    path.symlink_to(target)
    record = snapshot.document['files']['linux/drivers/link.c']
    record.update(target=target, bytes=len(target), sha256=hashlib.sha256(target.encode()).hexdigest())
    snapshot.repin()
    with pytest.raises(ValueError, match='escapes'):
        check(snapshot)


def test_link_cannot_be_replaced_with_a_regular_file(snapshot):
    path = snapshot.source / 'drivers/link.c'
    path.unlink()
    path.write_bytes(b'driver.c')
    path.chmod(0o777)
    with pytest.raises(ValueError, match='link type'):
        check(snapshot)


@pytest.mark.parametrize('field,value', [('revision', '0' * 40), ('diff_sha256', 'not-a-hash')])
def test_wrong_source_identity_is_not_relabelled_as_the_published_snapshot(snapshot, field, value):
    snapshot.document['source'][field] = value
    snapshot.repin()
    with pytest.raises(ValueError, match='revision or source fingerprint'):
        check(snapshot)


def test_added_source_hashes_must_describe_actual_released_files(snapshot):
    snapshot.document['source']['added_files_sha256']['drivers/driver.c'] = '0' * 64
    snapshot.repin()
    with pytest.raises(ValueError, match='Added-source identity'):
        check(snapshot)


def test_duplicate_json_properties_are_refused_before_inventory_acceptance(snapshot):
    content = snapshot.manifest.read_bytes()
    content = b'{"schema":"duplicate",' + content[1:]
    snapshot.manifest.write_bytes(content)
    save_json(snapshot.lock, {'schema': 'iwave.source-release.v1',
        'source_manifest_sha256': hashlib.sha256(content).hexdigest(), 'source_manifest_bytes': len(content)})
    with pytest.raises(ValueError, match='duplicate properties'):
        check(snapshot)


def test_bounded_json_reader_rejects_oversize_without_materializing_large_data(tmp_path):
    path = tmp_path / 'bounded.json'
    path.write_text('{"payload":"small"}')
    with pytest.raises(ValueError, match='bounded regular'):
        strict_document(path, path.stat().st_size - 1)


def test_manifest_itself_cannot_be_a_symlink(snapshot):
    alternate = snapshot.manifest.with_name('manifest-target.json')
    snapshot.manifest.rename(alternate)
    snapshot.manifest.symlink_to(alternate.name)
    with pytest.raises(ValueError, match='bounded regular'):
        check(snapshot)


def test_special_source_entry_is_refused(snapshot):
    os.mkfifo(snapshot.source / 'unexpected-fifo')
    with pytest.raises(ValueError, match='special file'):
        check(snapshot)


@pytest.fixture
def compact(tmp_path):
    baseline = tmp_path / 'baseline-root'
    path = baseline / 'baseline/stock-external-modules.json'
    receipt = {'native_count': 2, 'native_paths': ['drivers/a.ko', 'net/b.ko'],
        'rootfs_sha256': 'b' * 64, 'files': {}}
    def write():
        sha = save_json(path, receipt)
        return {'stock_module_inventory_sha256': sha}
    inputs = write()
    vendor = {'files': {'inputs/vendor/Linux/rootfs.tar.zst': {'sha256': 'b' * 64}}}
    return SimpleNamespace(baseline=baseline, path=path, receipt=receipt, inputs=inputs, vendor=vendor, write=write)


def test_compact_inventory_uses_all_native_names_without_retaining_native_binaries(compact):
    _, names = native_baseline_names(compact.baseline, compact.vendor, compact.inputs)
    assert names == {'drivers/a.ko', 'net/b.ko'}
    assert not (compact.baseline / 'baseline/kernel-set').exists()


def test_modified_compact_inventory_is_refused_after_compilation(compact):
    compact.receipt['native_paths'][0] = 'drivers/other.ko'
    compact.write()
    with pytest.raises(ValueError, match='build or stock-rootfs identity'):
        native_baseline_names(compact.baseline, compact.vendor, compact.inputs)


@pytest.mark.parametrize('mutation', ['rootfs', 'duplicate', 'traversal', 'nonmodule', 'count'])
def test_compact_inventory_rejects_unbound_or_ambiguous_native_names(compact, mutation):
    if mutation == 'rootfs':
        compact.receipt['rootfs_sha256'] = 'c' * 64
    elif mutation == 'duplicate':
        compact.receipt['native_paths'] = ['drivers/a.ko', 'drivers/a.ko']
    elif mutation == 'traversal':
        compact.receipt['native_paths'][0] = '../outside.ko'
    elif mutation == 'nonmodule':
        compact.receipt['native_paths'][0] = 'drivers/source.c'
    else:
        compact.receipt['native_count'] = 3
    inputs = compact.write()
    with pytest.raises(ValueError):
        native_baseline_names(compact.baseline, compact.vendor, inputs)


def test_legacy_baseline_tree_remains_supported_without_new_manifest_fields(tmp_path):
    baseline = tmp_path / 'legacy'
    save_json(baseline / 'baseline/stock-external-modules.json', {'native_count': 1, 'files': {}})
    save(baseline / 'baseline/kernel-set/lib/modules/old/kernel/drivers/a.ko', b'legacy native module')
    _, names = native_baseline_names(baseline, {}, {})
    assert names == {'drivers/a.ko'}


def test_build_identity_manifest_route_preserves_flexcan_and_energy_checks_without_git(snapshot, tmp_path, monkeypatch):
    project, baseline, toolchain = (tmp_path / name for name in ('project', 'baseline', 'toolchain'))
    lock = project / 'profiles/tch-g62h-imx91-rel3.3/source-release.json'
    save(lock, snapshot.lock.read_bytes())
    for path in ('components/flexcan-controller-policy/manifest.json',
                 'components/flexcan-controller-policy/include/uapi/linux/omam_flexcan.h'):
        save(project / path, b'public component input')
    for path in ('inputs/stock.config', 'inputs/manifest.json', 'toolchain/manifest.json',
                 'baseline/stock-external-modules.json'):
        save(baseline / path, b'{}')
    save(toolchain / 'usr/bin/aarch64-linux-gnu-gcc-14', b'public compiler fixture')
    calls = []
    def apply(source, *, check_only):
        assert source == snapshot.source and check_only is True
        calls.append('flexcan')
    def energy(source, *, check_only):
        assert source == snapshot.source and check_only is True
        calls.append('energy')
        return {'energy_patch_sha256': 'c' * 64}
    monkeypatch.setattr(build, 'REPO', project)
    monkeypatch.setattr(build, 'apply', apply)
    monkeypatch.setattr(build, 'energy', energy)
    monkeypatch.setattr(build.subprocess, 'run', lambda *a, **k: pytest.fail('Manifest route must not invoke Git'))
    result = build.identity(snapshot.source, baseline, toolchain, '6.12.34-test', snapshot.manifest)
    assert calls == ['flexcan', 'energy']
    assert result['source_manifest_sha256'] == hashlib.sha256(snapshot.manifest.read_bytes()).hexdigest()
    assert result['stock_module_inventory_sha256'] == hashlib.sha256(b'{}').hexdigest()
    assert result['energy_patch_sha256'] == 'c' * 64
    assert result['source_manifest_verifier_sha256'] == hashlib.sha256((ROOT / 'tools/kernel/source_identity.py').read_bytes()).hexdigest()


def test_legacy_git_route_still_refuses_a_wrong_revision_before_applying_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(build.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=b'0' * 40))
    monkeypatch.setattr(build, 'apply', lambda *a, **k: pytest.fail('Wrong Git SRCREV must be refused first'))
    with pytest.raises(ValueError, match='Wrong kernel SRCREV'):
        build.identity(tmp_path, tmp_path, tmp_path, '6.12.34-test')
