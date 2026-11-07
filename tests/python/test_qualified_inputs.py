# SPDX-License-Identifier: GPL-2.0-only
"""Public producer/consumer contracts with a complete tiny 814-module kernel set.

The files are intentionally synthetic, not executable modules. This tests receipt,
source/vendor identity and exact file-inventory acceptance after ABI qualification;
it does not replace the producer's ELF, compiler or hardware checks.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'tools/image'))
import qualified_inputs as subject
import qualify_profile
import build_base

VENDOR_NAMES = (
    'Firmware/Image_uuu', 'Firmware/flash_uuu.bin',
    'Firmware/imx91-iwg50m-prgjj_uuu.dtb', 'Firmware/initramfs.cpio.zst.u-boot',
    'Linux/Image', 'Linux/flash.bin', 'Linux/imx91-iwg62h-prgtt.dtb',
    'Linux/rootfs.tar.zst', 'iG-PRGTT-TelematicsConnectHub.uuu',
)
OUTPUT_NAMES = ('Image', 'imx91-iwg62h-prgtt.dtb', 'config', 'Module.symvers',
                'System.map', 'module-symbols.symvers')
INDEX_NAMES = ('modules.dep', 'modules.dep.bin', 'modules.alias', 'modules.alias.bin',
               'modules.symbols', 'modules.symbols.bin', 'modules.builtin',
               'modules.builtin.modinfo', 'modules.order', 'updates/omam/manifest.json')
STOCK_NAMES = ('cryptodev.ko', 'igb_uio.ko', 'kpage_ncache.ko', 'mlan.ko', 'moal.ko')
IMU_NAMES = ('st_lsm6dsx.ko', 'st_lsm6dsx_i2c.ko')
EXPECTED_COUNTS = {'native_modules': 807, 'stock_modules': 5,
                   'imu_modules': 2, 'total_modules': 814}
QUALIFIED = 'PASS_BUILD_AND_STATIC_ABI'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def payload(root, name):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ('independent public fixture: ' + name + '\n').encode()
    path.write_bytes(data)
    return {'bytes': len(data), 'sha256': sha(data)}


def source_contract():
    """Read public component identities independently of the consumer helper."""
    energy = PROJECT / 'components/bq25601-poweroff'
    source = energy / 'source.json'
    declaration = json.loads(source.read_text())
    return {
        'srcrev': declaration['upstreamRevision'],
        'energy_patch_sha256': sha((energy / 'bq25601-ship-on-poweroff.patch').read_bytes()),
        'energy_source_manifest_sha256': sha(source.read_bytes()),
        'energy_driver_sha256': declaration['postSha256'],
        'flexcan_manifest_sha256': sha((PROJECT / 'components/flexcan-controller-policy/manifest.json').read_bytes()),
        'uapi_sha256': sha((PROJECT / 'components/flexcan-controller-policy/include/uapi/linux/omam_flexcan.h').read_bytes()),
    }


class InputSet:
    def __init__(self, root, monkeypatch):
        self.vendor_root = root / 'vendor'
        self.kernel_root = root / 'kernel'
        self.vendor_path = root / 'vendor-inputs.json'
        self.receipt_path = root / 'kernel-build.json'
        self.release = '6.12.34-independent-public-fixture'
        self.prefix = 'lib/modules/' + self.release + '/'
        self.sources = source_contract()
        vendor_files = {}
        for name in VENDOR_NAMES:
            vendor_files['inputs/vendor/' + name] = {
                **payload(self.vendor_root, name),
                'member': 'iG-PRGTT-BN-R2.0-REL3.3_Telematics_Connect_Hub/' + name,
            }
        self.rootfs_sha = vendor_files['inputs/vendor/Linux/rootfs.tar.zst']['sha256']
        # The production byte-pinning validator still executes; the fixture supplies
        # a tiny expected stock payload instead of the multi-gigabyte real archive.
        monkeypatch.setattr(subject, 'REL33_ROOTFS_SHA256', self.rootfs_sha)
        self.vendor_manifest = {
            'schema': 'iwave.rel33.preparation-inputs.v1', 'status': 'ready',
            'archive_sha256': 'a1ccde9b961bfbfe1a7e4efa8da75eab6342d7b8b0723e9dd28d6032a5893d43',
            'files': vendor_files,
        }
        save(self.vendor_path, self.vendor_manifest)
        self.vendor = subject.validate_vendor(self.vendor_root, self.vendor_path)
        outputs = {name: payload(self.kernel_root, name) for name in OUTPUT_NAMES}
        self.native = [self.prefix + f'kernel/drivers/fixture/native-{i:03d}.ko' for i in range(807)]
        self.stock = [self.prefix + 'updates/stock/' + name for name in STOCK_NAMES]
        self.imu = [self.prefix + 'updates/omam/' + name for name in IMU_NAMES]
        modules = {name: payload(self.kernel_root, name) for name in self.native + self.stock + self.imu}
        metadata = {self.prefix + name: payload(self.kernel_root, self.prefix + name) for name in INDEX_NAMES}
        inputs = {**self.sources, 'kernel_release': self.release,
                  'vendor_receipt_sha256': sha(self.vendor_path.read_bytes())}
        self.manifest = {
            'schema': 'omam.kernel-set.v1', 'release': self.release,
            'static_qualification': QUALIFIED, 'inputs': inputs,
            'outputs': outputs, 'modules': modules, 'module_metadata': metadata,
            'stock_external_provenance': {
                'rootfs_sha256': self.rootfs_sha,
                'files': {name: copy.deepcopy(modules[name]) for name in self.stock},
            },
        }
        self.receipt = {
            'schema': 'iwave.rel33.kernel-build.v2', 'status': QUALIFIED,
            'inputs': copy.deepcopy(inputs), 'config_sha256': outputs['config']['sha256'],
            'qualification': {'status': QUALIFIED, **EXPECTED_COUNTS,
                              'outputs': copy.deepcopy(outputs), 'kernel_manifest': {}},
        }
        self.commit_fixture_metadata()

    def commit_fixture_metadata(self):
        """Rebind intentional fixture edits, so later assertions reach their target check."""
        path = self.kernel_root / 'kernel-manifest.json'
        save(path, self.manifest)
        self.receipt['qualification']['kernel_manifest']['sha256'] = sha(path.read_bytes())
        save(self.receipt_path, self.receipt)

    def validate(self):
        return subject.validate_kernel(self.kernel_root, self.receipt_path, self.vendor, self.sources)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    return InputSet(tmp_path, monkeypatch)


@pytest.mark.parametrize('schema', ['iwave.rel33.kernel-build.v2', 'omam.rel33.kernel-build.v2'])
def test_public_and_legacy_producer_receipts_accept_the_complete_kernel_contract(inputs, schema):
    inputs.receipt['schema'] = schema
    save(inputs.receipt_path, inputs.receipt)
    result = inputs.validate()
    assert result['counts'] == EXPECTED_COUNTS
    assert len(result['manifest']['modules']) == 814
    assert result['manifest']['outputs'] == inputs.manifest['outputs']
    assert result['manifest_sha256'] == sha((inputs.kernel_root / 'kernel-manifest.json').read_bytes())


@pytest.mark.parametrize('schema', ['iwave.rel33.preparation-inputs.v1', 'omam.rel33.preparation-inputs.v1'])
def test_public_and_legacy_vendor_receipts_bind_all_nine_files(inputs, schema):
    inputs.vendor_manifest['schema'] = schema
    save(inputs.vendor_path, inputs.vendor_manifest)
    vendor = subject.validate_vendor(inputs.vendor_root, inputs.vendor_path)
    assert set(vendor['files']) == set(VENDOR_NAMES)
    assert vendor['files']['Linux/rootfs.tar.zst']['sha256'] == inputs.rootfs_sha


@pytest.mark.parametrize('schema', ['iwave.rel34.kernel-build.v2', 'iwave.rel33.kernel-build.v1', None])
def test_unknown_kernel_receipt_schema_is_refused(inputs, schema):
    inputs.receipt['schema'] = schema
    save(inputs.receipt_path, inputs.receipt)
    with pytest.raises(ValueError, match='completed kernel build'):
        inputs.validate()


def test_unknown_vendor_receipt_schema_is_refused(inputs):
    inputs.vendor_manifest['schema'] = 'iwave.unknown-preparation.v1'
    save(inputs.vendor_path, inputs.vendor_manifest)
    with pytest.raises(ValueError, match='preparation manifest'):
        subject.validate_vendor(inputs.vendor_root, inputs.vendor_path)


def test_vendor_archive_identity_cannot_be_relabelled(inputs):
    inputs.vendor_manifest['archive_sha256'] = '0' * 64
    save(inputs.vendor_path, inputs.vendor_manifest)
    with pytest.raises(ValueError, match='original archive identity'):
        subject.validate_vendor(inputs.vendor_root, inputs.vendor_path)


def test_changed_vendor_payload_is_refused(inputs):
    (inputs.vendor_root / 'Firmware/Image_uuu').write_bytes(b'changed recovery bytes')
    with pytest.raises(ValueError, match='bytes differ'):
        subject.validate_vendor(inputs.vendor_root, inputs.vendor_path)


def test_same_files_with_a_different_stock_rootfs_pin_are_refused(inputs):
    with pytest.raises(ValueError, match='different stock rootfs'):
        subject.validate_vendor(inputs.vendor_root, inputs.vendor_path, rootfs_pin='0' * 64)


def test_malformed_qualification_is_refused(inputs):
    inputs.receipt['qualification'] = []
    save(inputs.receipt_path, inputs.receipt)
    with pytest.raises(ValueError, match='qualification must be an object'):
        inputs.validate()


def test_unfinished_qualification_is_refused(inputs):
    inputs.receipt['qualification']['status'] = 'KERNEL_COMPILED'
    save(inputs.receipt_path, inputs.receipt)
    with pytest.raises(ValueError, match='finished qualification receipt'):
        inputs.validate()


def test_kernel_manifest_must_match_its_receipt_hash(inputs):
    inputs.manifest['extra'] = 'unacknowledged edit'
    save(inputs.kernel_root / 'kernel-manifest.json', inputs.manifest)
    with pytest.raises(ValueError, match='finished qualification receipt'):
        inputs.validate()


def test_manifest_and_receipt_source_inputs_must_agree(inputs):
    inputs.receipt['inputs']['energy_patch_sha256'] = '0' * 64
    save(inputs.receipt_path, inputs.receipt)
    with pytest.raises(ValueError, match='source/release identity'):
        inputs.validate()


def test_rebound_receipt_cannot_change_the_required_release_sources(inputs):
    for document in (inputs.receipt['inputs'], inputs.manifest['inputs']):
        document['energy_driver_sha256'] = '0' * 64
    inputs.commit_fixture_metadata()
    with pytest.raises(ValueError, match='BQ25601 and FlexCAN'):
        inputs.validate()


def test_receipt_cannot_bind_another_vendor_preparation(inputs):
    for document in (inputs.receipt['inputs'], inputs.manifest['inputs']):
        document['vendor_receipt_sha256'] = '0' * 64
    inputs.commit_fixture_metadata()
    with pytest.raises(ValueError, match='source/release identity'):
        inputs.validate()


def test_complete_output_declarations_are_required(inputs):
    del inputs.manifest['outputs']['System.map']
    del inputs.receipt['qualification']['outputs']['System.map']
    inputs.commit_fixture_metadata()
    with pytest.raises(ValueError, match='output identity is incomplete'):
        inputs.validate()


def test_changed_output_bytes_are_refused(inputs):
    (inputs.kernel_root / 'Image').write_bytes(b'changed kernel Image')
    with pytest.raises(ValueError, match='bytes differ'):
        inputs.validate()


def test_changed_module_bytes_are_refused(inputs):
    (inputs.kernel_root / inputs.native[0]).write_bytes(b'changed native module')
    with pytest.raises(ValueError, match='bytes differ'):
        inputs.validate()


def test_missing_native_module_is_not_accepted_as_a_smaller_qualified_set(inputs):
    name = inputs.native.pop()
    (inputs.kernel_root / name).unlink()
    del inputs.manifest['modules'][name]
    inputs.receipt['qualification'].update(native_modules=806, total_modules=813)
    inputs.commit_fixture_metadata()
    with pytest.raises(ValueError, match='all 807 native'):
        inputs.validate()


def test_count_booleans_do_not_satisfy_integer_qualification_fields(inputs):
    inputs.receipt['qualification']['stock_modules'] = True
    save(inputs.receipt_path, inputs.receipt)
    with pytest.raises(ValueError, match='all 807 native'):
        inputs.validate()


def test_stock_provenance_is_checked_after_rebinding_manifest_metadata(inputs):
    inputs.manifest['stock_external_provenance']['files'][inputs.stock[0]]['sha256'] = '0' * 64
    inputs.commit_fixture_metadata()
    with pytest.raises(ValueError, match='stock module differs from its provenance'):
        inputs.validate()


def test_stock_modules_must_come_from_the_selected_rootfs(inputs):
    inputs.manifest['stock_external_provenance']['rootfs_sha256'] = '0' * 64
    inputs.commit_fixture_metadata()
    with pytest.raises(ValueError, match='all 807 native'):
        inputs.validate()


def test_missing_required_module_index_is_refused_even_when_inventory_is_rebound(inputs):
    name = inputs.prefix + 'modules.dep.bin'
    (inputs.kernel_root / name).unlink()
    del inputs.manifest['module_metadata'][name]
    inputs.commit_fixture_metadata()
    with pytest.raises(ValueError, match='missing required module indexes'):
        inputs.validate()


def test_unqualified_extra_file_is_refused(inputs):
    (inputs.kernel_root / 'unexpected.ko').write_bytes(b'extra module')
    with pytest.raises(ValueError, match='missing or unqualified files'):
        inputs.validate()


def test_symlink_cannot_replace_an_output_with_the_same_bytes(inputs, tmp_path):
    image = inputs.kernel_root / 'Image'
    outside = tmp_path / 'external-image'
    image.rename(outside)
    image.symlink_to(outside)
    with pytest.raises(ValueError, match='link or special file'):
        inputs.validate()


def test_duplicate_json_receipt_properties_are_refused(inputs):
    original = inputs.receipt_path.read_text()
    inputs.receipt_path.write_text('{"schema":"iwave.rel33.kernel-build.v2",' + original[1:])
    with pytest.raises(ValueError, match='duplicate properties'):
        inputs.validate()


@pytest.fixture
def policy_boundary(inputs, monkeypatch, tmp_path):
    profile = PROJECT / 'profiles/tch-g62h-imx91-rel3.3'
    maintained = profile / 'image-policy.json'
    before = maintained.read_bytes()
    policy = json.loads(before)
    calls = []
    def sdk_acquisition(directory, lock, global_selection, publications):
        calls.append((directory, lock, global_selection, publications))
        assert lock == profile / 'dotnet-sdk.lock.json'
        assert global_selection == profile / 'global.json'
        assert publications == {}
        return {'tree_sha256': policy['sdk_tree_sha256']}
    monkeypatch.setattr(qualify_profile, 'validate_sdk', sdk_acquisition)
    return SimpleNamespace(profile=profile, maintained=maintained, before=before,
                           sdk=tmp_path / 'synthetic-sdk-boundary', output=tmp_path / 'candidate-policy.json', calls=calls)


def qualify(inputs, boundary):
    return qualify_profile.qualify(inputs.vendor_root, inputs.vendor_path, inputs.kernel_root,
        inputs.receipt_path, boundary.sdk, boundary.output)


def test_candidate_policy_uses_real_qualified_kernel_and_keeps_default_release_unchanged(inputs, policy_boundary):
    result = qualify(inputs, policy_boundary)
    assert len(policy_boundary.calls) == 1
    assert json.loads(policy_boundary.output.read_text()) == result
    assert result['kernel_release'] == inputs.release
    assert result['kernel_manifest_sha256'] == sha((inputs.kernel_root / 'kernel-manifest.json').read_bytes())
    assert result['kernel_outputs'] == inputs.manifest['outputs']
    assert result['physical_acceptance'] == 'NOT_RUN'
    assert policy_boundary.maintained.read_bytes() == policy_boundary.before
    default, default_path = build_base.image_policy(policy_boundary.profile)
    selected, selected_path = build_base.image_policy(policy_boundary.profile, policy_boundary.output)
    assert default_path == policy_boundary.maintained and default == json.loads(policy_boundary.before)
    assert selected_path == policy_boundary.output and selected['kernel_release'] == inputs.release
    assert default['kernel_release'] != selected['kernel_release']


def test_candidate_policy_does_not_overwrite_existing_output(inputs, policy_boundary):
    policy_boundary.output.write_bytes(b'owner existing policy')
    with pytest.raises(FileExistsError):
        qualify(inputs, policy_boundary)
    assert policy_boundary.output.read_bytes() == b'owner existing policy'
    assert policy_boundary.maintained.read_bytes() == policy_boundary.before


def test_candidate_policy_refuses_sdk_change(inputs, policy_boundary, monkeypatch):
    monkeypatch.setattr(qualify_profile, 'validate_sdk', lambda *_args: {'tree_sha256': '0' * 64})
    with pytest.raises(ValueError, match='silently change the pinned SDK'):
        qualify(inputs, policy_boundary)
    assert not policy_boundary.output.exists()
    assert policy_boundary.maintained.read_bytes() == policy_boundary.before


def test_candidate_policy_checks_actual_kernel_bytes_before_sdk_acquisition(inputs, policy_boundary):
    (inputs.kernel_root / inputs.imu[0]).write_bytes(b'changed qualified IMU module')
    with pytest.raises(ValueError, match='bytes differ'):
        qualify(inputs, policy_boundary)
    assert policy_boundary.calls == []
    assert not policy_boundary.output.exists()
    assert policy_boundary.maintained.read_bytes() == policy_boundary.before
