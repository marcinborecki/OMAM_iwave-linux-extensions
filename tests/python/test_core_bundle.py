# SPDX-License-Identifier: GPL-2.0-only
"""Actual small 814-module payloads protect GPL-core omission and exact BSP restoration."""
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'tools/kernel'))
import core_bundle as subject
from test_qualified_inputs import InputSet, EXPECTED_COUNTS


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(subject.json_bytes(value))


def record(path):
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    result = InputSet(tmp_path / 'inputs', monkeypatch)
    release = json.loads((PROJECT / 'profiles/tch-g62h-imx91-rel3.3/source-release.json').read_text())
    result.manifest['inputs'].update(source_manifest_sha256=release['source_manifest_sha256'],
        source_manifest_bytes=release['source_manifest_bytes'])
    result.receipt['inputs'] = copy.deepcopy(result.manifest['inputs'])
    result.receipt['commands'] = [{'argv': ['/fixture/private/work', 'synthetic-local-build-only']}]
    result.receipt['resources'] = {'local_scope': '/fixture/private/scope'}
    result.receipt['dependencies'] = ['synthetic-local-only']
    set_config(result, b'CONFIG_INITRAMFS_SOURCE=""\nCONFIG_EXTRA_FIRMWARE=""\nCONFIG_TEST=y\n')
    return result


def set_config(inputs, data):
    path = inputs.kernel_root / 'config'
    path.write_bytes(data)
    item = record(path)
    inputs.manifest['outputs']['config'] = item
    inputs.receipt['config_sha256'] = item['sha256']
    inputs.receipt['qualification']['outputs']['config'] = copy.deepcopy(item)
    inputs.commit_fixture_metadata()


def unpack(path, root):
    data = subprocess.run(['zstd', '-qdc', str(path)], capture_output=True, check=True, timeout=30).stdout
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        archive.extractall(root, filter='data')
    return root / 'iwave-kernel-core'


def pack(inputs, path, release_record=None):
    return subject.pack(inputs.kernel_root, inputs.receipt_path, inputs.vendor_root,
        inputs.vendor_path, path, record=release_record)


@pytest.fixture
def bundle(tmp_path, inputs):
    output, trusted = tmp_path / 'core.tar.zst', tmp_path / 'release.json'
    publication = pack(inputs, output, trusted)
    root = unpack(output, tmp_path / 'unpacked')
    baseline = tmp_path / 'baseline'
    path = baseline / 'inputs/manifest.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(inputs.vendor_path.read_bytes())
    files = {}
    for name in inputs.stock:
        basename = Path(name).name
        retained = 'baseline/stock-modules/' + basename
        target = baseline / retained
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((inputs.kernel_root / name).read_bytes())
        files[basename] = {**record(target), 'member': 'usr/' + name, 'retained_path': retained}
    save_json(baseline / 'baseline/stock-external-modules.json',
        {'native_count': 807, 'rootfs_sha256': inputs.rootfs_sha, 'files': files})
    return root, baseline, trusted, publication


def restore(bundle, inputs):
    root, baseline, trusted, _ = bundle
    return subject.restore(root, baseline, inputs.vendor_root, trusted)


def missing_paths(bundle, inputs):
    return [bundle[0] / 'kernel' / n for n in inputs.stock]


def rebind_delivery(bundle):
    root, _, trusted, _ = bundle
    data = json.loads(trusted.read_text())
    data['delivery_sha256'] = subject.digest(root / 'delivery.json')
    save_json(trusted, data)


def test_pack_delivers_809_modules_but_never_the_five_factory_binaries(bundle, inputs):
    root, _, trusted, publication = bundle
    delivery = json.loads((root / 'delivery.json').read_text())
    modules = sorted((root / 'kernel').rglob('*.ko'))
    assert len(modules) == 809
    assert {p.name for p in modules}.isdisjoint({'cryptodev.ko', 'igb_uio.ko', 'kpage_ncache.ko', 'mlan.ko', 'moal.ko'})
    assert set(delivery['missing_factory_modules']) == set(inputs.stock)
    assert len(delivery['missing_factory_modules']) == 5
    assert publication['delivered_modules'] == 809 and publication['restored_modules'] == 814
    assert json.loads(trusted.read_text()) == publication
    assert publication['delivery_sha256'] == subject.digest(root / 'delivery.json')
    assert 'archive' not in delivery and 'delivery.json' not in delivery['files']
    assert set(subject.regular_files(root)) == set(delivery['files']) | {'delivery.json'}
    assert b'GNU GENERAL PUBLIC LICENSE' in (root / 'LICENSE').read_bytes()
    assert b'factory external modules are omitted' in (root / 'NOTICE').read_bytes()
    reference = json.loads((root / 'SOURCE.json').read_text())
    published = json.loads((PROJECT / 'profiles/tch-g62h-imx91-rel3.3/source-release.json').read_text())
    assert reference['archive']['sha256'] == published['archive']['sha256']
    assert reference['archive']['url'] == published['archive']['url']
    assert reference['source_manifest_sha256'] == published['source_manifest_sha256']
    receipt = json.loads((root / 'kernel-build.json').read_text())
    assert set(receipt) == {'schema', 'status', 'inputs', 'config_sha256', 'qualification', 'physical_loading', 'hardware_readback'}
    assert set(receipt['qualification']) == {'status', 'kernel_manifest', 'outputs', *EXPECTED_COUNTS}
    assert '/fixture/private/' not in json.dumps(receipt)
    assert all((inputs.kernel_root / name).is_file() for name in inputs.stock)


def test_restore_adds_exact_five_and_passes_actual_full_kernel_validation_idempotently(bundle, inputs):
    root = bundle[0]
    existing = {name: subject.digest(root / name) for name in subject.regular_files(root)}
    result = restore(bundle, inputs)
    assert result['status'] == 'PASS_RESTORED_COMPLETE_KERNEL'
    assert result['counts'] == EXPECTED_COUNTS and result['restored_files'] == 5
    assert len(list((root / 'kernel').rglob('*.ko'))) == 814
    for name, digest in existing.items():
        assert subject.digest(root / name) == digest
    receipt = root / 'kernel-build.json'
    vendor = subject.validate_vendor(inputs.vendor_root, bundle[1] / 'inputs/manifest.json')
    accepted = subject.validate_kernel(root / 'kernel', receipt, vendor, inputs.sources)
    assert accepted['counts'] == EXPECTED_COUNTS
    repeated = restore(bundle, inputs)
    assert repeated['idempotent'] is True and repeated['restored_files'] == 0


def test_tampered_delivered_payload_is_rejected_before_factory_copy(bundle, inputs):
    (bundle[0] / 'kernel/Image').write_bytes(b'changed image')
    with pytest.raises(ValueError, match='bytes differ'):
        restore(bundle, inputs)
    assert not any(p.exists() for p in missing_paths(bundle, inputs))


def test_delivery_must_match_independently_trusted_release_record(bundle, inputs):
    root, _, trusted, _ = bundle
    value = json.loads(trusted.read_text())
    value['delivery_sha256'] = '0' * 64
    save_json(trusted, value)
    with pytest.raises(ValueError, match='trusted release record'):
        restore(bundle, inputs)
    assert not any(p.exists() for p in missing_paths(bundle, inputs))


def test_changed_bsp_is_refused_without_factory_writes(bundle, inputs):
    (inputs.vendor_root / 'Firmware/Image_uuu').write_bytes(b'changed vendor input')
    with pytest.raises(ValueError, match='bytes differ'):
        restore(bundle, inputs)
    assert not any(p.exists() for p in missing_paths(bundle, inputs))


def test_different_valid_vendor_manifest_is_not_silently_rebound(bundle, inputs):
    path = bundle[1] / 'inputs/manifest.json'
    value = json.loads(path.read_text())
    value['schema'] = 'omam.rel33.preparation-inputs.v1'
    save_json(path, value)
    with pytest.raises(ValueError, match='Supplied BSP differs'):
        restore(bundle, inputs)


@pytest.mark.parametrize('kind', ['symlink', 'extra'])
def test_extra_owner_file_or_delivered_symlink_is_refused_and_preserved(bundle, inputs, kind):
    root = bundle[0]
    if kind == 'symlink':
        path = root / 'kernel/Image'
        path.unlink()
        path.symlink_to(inputs.kernel_root / 'Image')
    else:
        path = root / 'owner-notes.txt'
        path.write_bytes(b'owner notes must not be removed')
    with pytest.raises(ValueError):
        restore(bundle, inputs)
    assert path.is_symlink() if kind == 'symlink' else path.read_bytes() == b'owner notes must not be removed'
    assert not any(p.exists() for p in missing_paths(bundle, inputs))


def test_unsafe_declaration_is_refused_even_after_rebinding_the_release_record(bundle, inputs):
    root = bundle[0]
    value = json.loads((root / 'delivery.json').read_text())
    value['files']['../outside'] = value['files']['LICENSE']
    save_json(root / 'delivery.json', value)
    rebind_delivery(bundle)
    with pytest.raises(ValueError, match='unsafe relative path'):
        restore(bundle, inputs)


def test_factory_source_symlink_is_refused_before_copy(bundle, inputs):
    path = bundle[1] / 'baseline/stock-modules/cryptodev.ko'
    path.unlink()
    path.symlink_to(inputs.kernel_root / inputs.stock[0])
    with pytest.raises(ValueError):
        restore(bundle, inputs)
    assert not any(p.exists() for p in missing_paths(bundle, inputs))


def test_partial_preexisting_factory_restore_is_preserved_and_refused(bundle, inputs):
    target = missing_paths(bundle, inputs)[0]
    target.parent.mkdir(parents=True)
    target.write_bytes((inputs.kernel_root / inputs.stock[0]).read_bytes())
    original = target.read_bytes()
    with pytest.raises(ValueError, match='partial factory-module'):
        restore(bundle, inputs)
    assert target.read_bytes() == original
    assert not any(p.exists() for p in missing_paths(bundle, inputs)[1:])


def test_changed_preexisting_restored_module_is_never_overwritten(bundle, inputs):
    restore(bundle, inputs)
    path = missing_paths(bundle, inputs)[0]
    path.write_bytes(b'owner modified this module')
    with pytest.raises(ValueError):
        restore(bundle, inputs)
    assert path.read_bytes() == b'owner modified this module'


def test_final_consumer_failure_rolls_back_only_owned_factory_additions(bundle, inputs, monkeypatch, tmp_path):
    marker = tmp_path / 'owner-file'
    marker.write_bytes(b'preserve owner work')
    def failed(*args, **kwargs):
        raise ValueError('synthetic final qualification failure')
    monkeypatch.setattr(subject, 'validate_kernel', failed)
    with pytest.raises(ValueError, match='synthetic final qualification'):
        restore(bundle, inputs)
    assert not any(p.exists() for p in missing_paths(bundle, inputs))
    assert (bundle[0] / 'kernel/Image').is_file()
    assert marker.read_bytes() == b'preserve owner work'


@pytest.mark.parametrize('setting', ['CONFIG_INITRAMFS_SOURCE', 'CONFIG_EXTRA_FIRMWARE'])
def test_pack_refuses_separately_embedded_payload_even_with_consistent_qualification_metadata(inputs, tmp_path, setting):
    values = {'CONFIG_INITRAMFS_SOURCE': '', 'CONFIG_EXTRA_FIRMWARE': ''}
    values[setting] = 'separate-payload'
    set_config(inputs, ''.join(f'{key}="{value}"\n' for key, value in values.items()).encode())
    with pytest.raises(ValueError, match='explicitly empty'):
        pack(inputs, tmp_path / 'bad.tar.zst')
    assert not (tmp_path / 'bad.tar.zst').exists()


@pytest.mark.parametrize('kind', ['output', 'parent', 'record'])
def test_pack_refuses_symlink_outputs_and_keeps_owner_paths(inputs, tmp_path, kind):
    owned = tmp_path / 'owner'
    owned.mkdir()
    output, record_path = tmp_path / 'core.tar.zst', None
    if kind == 'output':
        output.symlink_to(owned / 'target')
    elif kind == 'parent':
        alias = tmp_path / 'alias'
        alias.symlink_to(owned, target_is_directory=True)
        output = alias / 'core.tar.zst'
    else:
        record_path = tmp_path / 'record.json'
        record_path.symlink_to(owned / 'target')
    with pytest.raises(ValueError, match='output|symlink'):
        pack(inputs, output, record_path)
    assert list(owned.iterdir()) == []


def test_pack_refuses_unknown_private_input_fields(inputs, tmp_path):
    inputs.manifest['inputs']['local_work_directory'] = '/fixture/private/work'
    inputs.receipt['inputs'] = copy.deepcopy(inputs.manifest['inputs'])
    inputs.commit_fixture_metadata()
    with pytest.raises(ValueError, match='public identity contract'):
        pack(inputs, tmp_path / 'bad.tar.zst')


def test_compression_failure_cleans_owned_partial_archive_and_process(inputs, tmp_path, monkeypatch):
    original = subprocess.Popen
    children = []
    def failed(command, *args, **kwargs):
        if 'zstd' in command:
            child = original([sys.executable, '-c', 'import sys; sys.stdin.buffer.read(); sys.exit(23)'], *args, **kwargs)
            children.append(child)
            return child
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', failed)
    output = tmp_path / 'failed.tar.zst'
    with pytest.raises(ValueError, match='compression failed'):
        pack(inputs, output)
    assert children and all(p.poll() is not None for p in children)
    assert not output.exists() and not list(tmp_path.glob('.kernel-core-*'))


def test_output_bound_rejects_oversized_archive_without_publication(inputs, tmp_path):
    output = tmp_path / 'too-small.tar.zst'
    with pytest.raises((OSError, ValueError)):
        subject.pack(inputs.kernel_root, inputs.receipt_path, inputs.vendor_root, inputs.vendor_path,
            output, max_output_bytes=16)
    assert not output.exists() and not list(tmp_path.glob('.kernel-core-*'))
