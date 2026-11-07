"""An external BSP becomes usable without any original private acquisition receipt."""
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/image'))
spec = importlib.util.spec_from_file_location('public_prepare_inputs', ROOT / 'tools/kernel/prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
from qualified_inputs import validate_vendor


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def inputs(tmp_path):
    vendor, compiler_root, profile = (tmp_path / name for name in ('vendor', 'toolchain', 'profile'))
    vendor.mkdir(); compiler_root.mkdir(); profile.mkdir()
    modules = {name: ('factory fixture ' + name).encode() for name in
               ('cryptodev.ko', 'igb_uio.ko', 'kpage_ncache.ko', 'mlan.ko', 'moal.ko')}
    raw = io.BytesIO()
    prefix = 'usr/lib/modules/6.12.34-lts-next-gb1aec8c1dd0d/'
    with tarfile.open(fileobj=raw, mode='w') as archive:
        for name, data in {**{prefix + f'kernel/drivers/module{i}.ko': b'native fixture' for i in range(807)},
                           **{prefix + 'updates/' + name: data for name, data in modules.items()}}.items():
            entry = tarfile.TarInfo(name); entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    rootfs = subprocess.run(['zstd', '-q', '-c'], input=raw.getvalue(), capture_output=True, check=True, timeout=20).stdout
    config = b'# independently supplied stock configuration\nCONFIG_MODVERSIONS=y\n'
    for name in prepare.VENDOR_FILES:
        path = vendor / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rootfs if name == 'Linux/rootfs.tar.zst' else
                         b'Image header IKCFG_ST' + gzip.compress(config) + b'IKCFG_ED' if name == 'Linux/Image' else
                         ('vendor fixture ' + name).encode())
    records = {name: {'bytes': (vendor / name).stat().st_size, 'sha256': sha((vendor / name).read_bytes()),
                     'member': 'iG-PRGTT-BN-R2.0-REL3.3_Telematics_Connect_Hub/' + name} for name in prepare.VENDOR_FILES}
    (profile / 'vendor-files.lock.json').write_text(json.dumps({'schema': 'iwave.vendor-files.v1',
        'archive_sha256': 'a1ccde9b961bfbfe1a7e4efa8da75eab6342d7b8b0723e9dd28d6032a5893d43', 'files': records}))
    compiler = compiler_root / 'usr/bin/aarch64-linux-gnu-gcc-14'
    compiler.parent.mkdir(parents=True); compiler.write_bytes(b'pinned synthetic compiler, never executed')
    (profile / 'build-toolchain.json').write_text(json.dumps({'compiler_sha256': sha(compiler.read_bytes())}))
    patch = profile / 'vendor-patches/example.patch'; patch.parent.mkdir(); patch.write_bytes(b'GPL fixture patch')
    (profile / 'source-lock.json').write_text(json.dumps({'revision': '02cf250c2e6bd375c11dae324ea145a39015d41b',
        'patches': [{'file': 'example.patch', 'sha256': sha(patch.read_bytes())}]}))
    (profile / 'source-gaps.json').write_text(json.dumps({'external_modules': [
        {'module': name, 'sha256': sha(data)} for name, data in modules.items()]}))
    return vendor, compiler_root, tmp_path / 'baseline', profile, config


def test_public_preparation_is_consumed_by_the_real_vendor_validator(inputs):
    vendor, toolchain, output, profile, config = inputs
    result = prepare.prepare(vendor, toolchain, output, profile)
    assert result['native_modules'] == 807 and result['preserved_external_modules'] == 5
    assert (output / 'inputs/stock.config').read_bytes() == config
    inventory = json.loads((output / 'baseline/stock-external-modules.json').read_text())
    assert len(inventory['native_paths']) == 807
    assert inventory['native_paths'][0] == 'drivers/module0.ko'
    assert len(list((output / 'baseline').rglob('*.ko'))) == 5
    validated = validate_vendor(vendor, output / 'inputs/manifest.json',
                                rootfs_pin=sha((vendor / 'Linux/rootfs.tar.zst').read_bytes()))
    assert len(validated['files']) == 9


@pytest.mark.parametrize('name', ['Linux/rootfs.tar.zst', 'Firmware/flash_uuu.bin'])
def test_changed_bsp_is_refused_without_creating_a_baseline(inputs, name):
    vendor, toolchain, output, profile, _ = inputs
    (vendor / name).write_bytes(b'changed input')
    with pytest.raises(ValueError, match='bytes differ'):
        prepare.prepare(vendor, toolchain, output, profile)
    assert not output.exists()


def test_changed_compiler_is_not_silently_requalified(inputs):
    vendor, toolchain, output, profile, _ = inputs
    (toolchain / 'usr/bin/aarch64-linux-gnu-gcc-14').write_bytes(b'another compiler')
    with pytest.raises(ValueError, match='pinned native'):
        prepare.prepare(vendor, toolchain, output, profile)
    assert not output.exists()


def test_failed_stock_module_identity_removes_only_owned_partial_output(inputs):
    vendor, toolchain, output, profile, _ = inputs
    path = profile / 'source-gaps.json'; data = json.loads(path.read_text())
    data['external_modules'][0]['sha256'] = '0' * 64; path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Factory module differs'):
        prepare.prepare(vendor, toolchain, output, profile)
    assert not output.exists()
    assert vendor.is_dir()


def test_preparation_never_replaces_existing_owner_output(inputs):
    vendor, toolchain, output, profile, _ = inputs
    output.mkdir(); (output / 'owner.txt').write_text('keep')
    with pytest.raises(ValueError, match='must be new'):
        prepare.prepare(vendor, toolchain, output, profile)
    assert (output / 'owner.txt').read_text() == 'keep'


def test_bsp_only_preparation_needs_no_compiler_for_restoring_published_core(inputs):
    vendor, _, output, profile, config = inputs
    (profile / 'build-toolchain.json').unlink()
    result = prepare.prepare(vendor, None, output, profile)
    assert result['preserved_external_modules'] == 5
    assert (output / 'inputs/stock.config').read_bytes() == config
    assert not (output / 'toolchain').exists()
    assert (output / 'baseline/stock-external-modules.json').is_file()
