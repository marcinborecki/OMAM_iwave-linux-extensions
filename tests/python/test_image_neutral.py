"""Synthetic actual-archive tests for the public base and private application boundary."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/image'))
from compose_rootfs import compose, compose_overlay
from image_io import digest
from neutral_profile import stage_neutral, verify_neutral
from sdk_payload import tree_sha256
from verify_composed_rootfs import archive_entries, verify, verify_overlay
import build_base


def compressed_archive(path, files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as stream:
        parents = set()
        for name in files:
            parents.update(p.as_posix() for p in Path(name).parents if p.as_posix() != '.')
        for name in sorted(parents, key=lambda p: (len(p.split('/')), p)):
            member = tarfile.TarInfo(name)
            member.type, member.mode = tarfile.DIRTYPE, 0o755
            stream.addfile(member)
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            if isinstance(data, tuple):
                member.type, member.linkname, member.mode = tarfile.SYMTYPE, data[1], 0o777
                stream.addfile(member)
            else:
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
    data = subprocess.run(['zstd', '-q', '-c'], input=raw.getvalue(), capture_output=True, timeout=30, check=True).stdout
    path.write_bytes(data)
    return digest(path)


def write_file(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}


def stock_boot_files():
    return {
        'etc/systemd/system/default.target': ('symlink', '/usr/lib/systemd/system/graphical.target'),
        'etc/systemd/system/graphical.target.wants/weston.service': ('symlink', '/usr/lib/systemd/system/weston.service'),
        'etc/systemd/system/sockets.target.wants/weston.socket': ('symlink', '/usr/lib/systemd/system/weston.socket'),
        'etc/systemd/system/multi-user.target.wants/assm_conf.service': ('symlink', '/usr/lib/systemd/system/assm_conf.service'),
        'etc/systemd/system/multi-user.target.wants/battery_monitor.service': ('symlink', '/usr/lib/systemd/system/battery_monitor.service'),
        'usr/lib/systemd/system/multi-user.target': b'[Unit]\nDescription=Multi-user system\n',
        'usr/lib/systemd/system/battery_monitor.service': b'[Service]\nExecStart=/usr/bin/battery_monitor\nRestart=on-failure\n',
        'usr/lib/systemd/system/weston.service': b'[Service]\nWorkingDirectory=/home/weston\nExecStart=/usr/bin/weston\n',
        'usr/lib/systemd/system/assm_conf.service': b'[Service]\nExecStart=/usr/bin/assm_conf\n',
        'usr/lib/systemd/system/weston.socket': b'[Socket]\nListenStream=/run/wayland-0\n'}


def neutral_boot_files():
    return {
        'etc/systemd/system/default.target': ('symlink', '/usr/lib/systemd/system/multi-user.target'),
        'etc/systemd/system/weston.service': ('symlink', '/dev/null'),
        'etc/systemd/system/weston.socket': ('symlink', '/dev/null'),
        'etc/systemd/system/assm_conf.service': ('symlink', '/dev/null'),
        'etc/systemd/system/multi-user.target.wants/battery_monitor.service': ('symlink', '/usr/lib/systemd/system/battery_monitor.service'),
        'usr/lib/systemd/system/battery_monitor.service': b'[Service]\nExecStart=/usr/bin/battery_monitor\n',
        'usr/bin/battery_monitor': b'factory safety protection', 'usr/lib/libTelematics.so': b'vendor runtime'}


@pytest.fixture
def image(tmp_path):
    files = {'etc/shadow': b'root:stock-hash:1:0:99999:7:::\nservice:!:1:0:99999:7:::\n',
        'etc/gshadow': b'root:stock-hash::\n', 'etc/hostname': b'vendor-host\n',
        'etc/machine-id': b'stock-identity', 'etc/ppp/peers/quectel-chat-connect': b'AT+CGDCONT=1,"IP","test-apn.invalid"',
        'etc/ppp/quectel-pppd.sh': b'connect proprietary SIM',
        'root/networkconfig.xml': b'private-looking vendor APN sample', 'root/.ssh/authorized_keys': b'stock key',
        'etc/ssh/ssh_host_ed25519_key': b'stock host key',
        'usr/lib/modules/old/stock.ko': b'old ABI',
        'usr/bin/battery_monitor': b'factory safety protection', 'usr/lib/libTelematics.so': b'vendor runtime'}
    files.update(stock_boot_files())
    stock = tmp_path / 'stock.tar.zst'
    stock_hash = compressed_archive(stock, files)
    sdk = tmp_path / 'sdk'
    sdk.mkdir()
    (sdk / 'dotnet').write_bytes(b'synthetic SDK host')
    (sdk / 'dotnet').chmod(0o755)
    (sdk / 'sdk-payload-report.json').write_text(json.dumps({'schema_version': 1, 'rid': 'linux-arm64',
        'host_from_sdk': '11.0.100', 'tree_sha256': tree_sha256(sdk)}))
    kernel_root = tmp_path / 'kernel'
    release = '6.12.34-omam-neutral-test'
    outputs = {n: write_file(kernel_root, n, ('qualified ' + n).encode()) for n in
        ('Image', 'imx91-iwg62h-prgtt.dtb', 'config', 'Module.symvers', 'System.map', 'module-symbols.symvers')}
    module = 'lib/modules/' + release + '/kernel/driver.ko'
    modules = {module: write_file(kernel_root, module, b'qualified kernel module')}
    index = 'lib/modules/' + release + '/modules.dep'
    metadata = {index: write_file(kernel_root, index, b'kernel/driver.ko:\n')}
    manifest = {'release': release, 'outputs': outputs, 'modules': modules, 'module_metadata': metadata}
    (kernel_root / 'kernel-manifest.json').write_text(json.dumps(manifest))
    kernel = {'root': kernel_root, 'manifest': manifest, 'manifest_sha256': digest(kernel_root / 'kernel-manifest.json'),
        'receipt_sha256': 'a' * 64}
    return {'stock': stock, 'stock_hash': stock_hash, 'sdk': sdk, 'kernel': kernel, 'files': files}


def make_neutral(tmp_path, image):
    overlay = tmp_path / 'overlay'
    plan, _ = stage_neutral(image['stock'], overlay, image['kernel'])
    target = tmp_path / 'base.tar.zst'
    compose(image['stock'], image['stock_hash'], image['sdk'], target, 123, overlay, plan,
        profile_path='etc/profile.d/dotnet.sh')
    return target, overlay, plan


def test_neutral_archive_has_sdk_qualified_kernel_and_factory_safety_without_provisioning(tmp_path, image):
    target, overlay, plan = make_neutral(tmp_path, image)
    assert verify_neutral(target)['status'] == 'PASS_SCOPED_ARCHIVE_CONTENT'
    assert verify(image['stock'], target, image['sdk'], overlay, plan,
        profile_path='etc/profile.d/dotnet.sh')['status'] == 'PASS'
    actual = archive_entries(target)
    assert actual['usr/bin/battery_monitor']['sha256'] == hashlib.sha256(b'factory safety protection').hexdigest()
    assert actual['usr/lib/libTelematics.so']['sha256'] == hashlib.sha256(b'vendor runtime').hexdigest()
    assert actual['opt/dotnet/dotnet']['sha256'] == hashlib.sha256(b'synthetic SDK host').hexdigest()
    release = image['kernel']['manifest']['release']
    assert actual['usr/lib/modules/' + release + '/kernel/driver.ko']['sha256'] == hashlib.sha256(b'qualified kernel module').hexdigest()
    assert not any(n.startswith(('root/', 'opt/omam/', 'etc/ppp/peers/')) for n in actual)
    assert actual['etc/machine-id']['size'] == 0
    assert 'etc/profile.d/omam-dotnet.sh' not in actual
    assert actual['etc/systemd/system/default.target']['linkname'] == '/usr/lib/systemd/system/multi-user.target'
    for unit in ('weston.service', 'weston.socket', 'assm_conf.service'):
        assert actual['etc/systemd/system/' + unit]['linkname'] == '/dev/null'
    assert 'etc/systemd/system/graphical.target.wants/weston.service' not in actual
    assert 'etc/systemd/system/sockets.target.wants/weston.socket' not in actual
    assert 'etc/systemd/system/multi-user.target.wants/assm_conf.service' not in actual
    assert actual['etc/systemd/system/multi-user.target.wants/battery_monitor.service']['linkname'] == '/usr/lib/systemd/system/battery_monitor.service'


@pytest.mark.parametrize('name,data', [('opt/omam/agent', b'app'), ('etc/omam/settings.json', b'{}'),
    ('etc/systemd/system/omam-edge.service', b'unit'), ('etc/network/interfaces', b'APN=test-apn.invalid'),
    ('etc/ssh/ssh_host_rsa_key', b'host key'), ('etc/machine-id', b'nonempty'),
    ('etc/shadow', b'root:unlocked:1::::::\n')])
def test_independent_verifier_rejects_provisioning_in_actual_archive(tmp_path, name, data):
    files = {'etc/shadow': b'root:!:1::::::\n', 'etc/gshadow': b'root:!::\n',
        'etc/hostname': b'iwave-tch\n', 'etc/machine-id': b'', 'opt/dotnet/dotnet': b'sdk',
        'etc/profile.d/dotnet.sh': b'export DOTNET_ROOT=/opt/dotnet\n'}
    files.update(neutral_boot_files())
    files[name] = data
    path = tmp_path / 'bad.tar.zst'
    compressed_archive(path, files)
    with pytest.raises(ValueError):
        verify_neutral(path)


def test_application_overlay_does_not_reinject_or_mutate_sdk_kernel_or_factory_paths(tmp_path, image):
    base, _, _ = make_neutral(tmp_path, image)
    overlay = tmp_path / 'app-overlay'
    write_file(overlay, 'opt/omam/application', b'private application fixture')
    plan = tmp_path / 'app-plan.json'
    plan.write_text(json.dumps({'schema_version': 1, 'delete': [], 'replace': []}))
    target = tmp_path / 'private.tar.zst'
    compose_overlay(base, digest(base), target, overlay, plan, 456)
    assert verify_overlay(base, target, overlay, plan)['status'] == 'PASS'
    before, after = archive_entries(base), archive_entries(target)
    for name, record in before.items():
        assert after[name] == record
    assert after['opt/omam/application']['sha256'] == hashlib.sha256(b'private application fixture').hexdigest()
    with pytest.raises(ValueError, match='forbidden'):
        verify_neutral(target)


def test_private_overlay_cannot_replace_public_sdk_profile(tmp_path, image):
    base, _, _ = make_neutral(tmp_path, image)
    overlay = tmp_path / 'bad-overlay'
    write_file(overlay, 'etc/profile.d/dotnet.sh', b'mutated')
    plan = tmp_path / 'bad-plan.json'
    plan.write_text(json.dumps({'schema_version': 1, 'delete': [], 'replace': ['etc/profile.d/dotnet.sh']}))
    with pytest.raises(ValueError, match='protected SDK'):
        compose_overlay(base, digest(base), tmp_path / 'bad.tar.zst', overlay, plan)


def test_overlay_refuses_wrong_base_hash_without_publishing_output(tmp_path, image):
    overlay = tmp_path / 'empty-overlay'
    overlay.mkdir()
    plan = tmp_path / 'empty.json'
    plan.write_text(json.dumps({'schema_version': 1, 'delete': [], 'replace': []}))
    target = tmp_path / 'wrong.tar.zst'
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        compose_overlay(image['stock'], '0' * 64, target, overlay, plan)
    assert not target.exists()
    assert not list(tmp_path.glob('.rootfs-compose-*'))


def test_unknown_hardware_profile_is_rejected_before_inputs_are_opened(tmp_path):
    with pytest.raises(ValueError, match='Unknown'):
        build_base.validate_base(tmp_path / 'nonexistent', 'some-other-iwave')


def test_base_manifest_refuses_changed_bytes_before_any_private_layer(tmp_path):
    root = tmp_path / 'base'
    files = {name: write_file(root, name, ('base ' + name).encode()) for name in build_base.VENDOR_FILES}
    (root / 'base-manifest.json').write_text(json.dumps({'schema': 'iwave.linux-base.v1',
        'profile': 'tch-g62h-imx91-rel3.3', 'files': files,
        'privacy': {'status': 'PASS_SCOPED_ARCHIVE_CONTENT'}, 'verification': {'status': 'PASS'}}))
    (root / 'Linux/rootfs.tar.zst').write_bytes(b'owner-modified-image')
    with pytest.raises(ValueError, match='bytes differ'):
        build_base.validate_base(root)


def rewrite_member(path, name, content):
    decoded = subprocess.run(['zstd', '-qdc', str(path)], capture_output=True, timeout=30, check=True).stdout
    raw = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(decoded), mode='r:') as source, tarfile.open(fileobj=raw, mode='w') as target:
        for member in source:
            data = source.extractfile(member).read() if member.isfile() else None
            if member.name == name:
                if content is None:
                    continue
                if isinstance(content, tuple):
                    data, member.size = None, 0
                    member.type, member.linkname = tarfile.SYMTYPE, content[1]
                else:
                    data, member.size, member.type, member.linkname = content, len(content), tarfile.REGTYPE, ''
            target.addfile(member, io.BytesIO(data) if data is not None else None)
    path.write_bytes(subprocess.run(['zstd', '-qc'], input=raw.getvalue(), capture_output=True, timeout=30, check=True).stdout)


def payload_policy(image):
    return {'sdk_tree_sha256': tree_sha256(image['sdk']),
        'kernel_manifest_sha256': image['kernel']['manifest_sha256'],
        'kernel_release': image['kernel']['manifest']['release']}


def test_actual_base_bytes_reconstruct_sdk_and_check_every_kernel_module(tmp_path, image):
    from neutral_profile import verify_base_payload
    base, _, _ = make_neutral(tmp_path, image)
    result = verify_base_payload(base, payload_policy(image))
    assert result['status'] == 'PASS_ACTUAL_SDK_AND_KERNEL_BYTES'
    assert result['kernel_files'] == 2


@pytest.mark.parametrize('target,message', [('sdk', 'SDK bytes'), ('module', 'module bytes'), ('manifest', 'kernel manifest')])
def test_actual_payload_validation_rejects_changes_even_if_outer_manifest_hash_was_rewritten(tmp_path, image, target, message):
    from neutral_profile import verify_base_payload
    base, _, _ = make_neutral(tmp_path, image)
    release = image['kernel']['manifest']['release']
    names = {'sdk': 'opt/dotnet/dotnet', 'module': 'usr/lib/modules/' + release + '/kernel/driver.ko',
        'manifest': 'usr/share/iwave-linux-extensions/kernel/' + release + '/kernel-manifest.json'}
    rewrite_member(base, names[target], b'tampered bytes')
    with pytest.raises(ValueError, match=message):
        verify_base_payload(base, payload_policy(image))


@pytest.mark.parametrize('name,content,message', [
    ('etc/systemd/system/default.target', ('symlink', '/usr/lib/systemd/system/graphical.target'), 'headless'),
    ('etc/systemd/system/assm_conf.service', None, 'mask'),
    ('etc/systemd/system/weston.service', ('symlink', '/usr/lib/systemd/system/weston.service'), 'mask'),
    ('etc/systemd/system/weston.socket', None, 'mask'),
    ('etc/systemd/system/multi-user.target.wants/battery_monitor.service', None, 'monitor enabled'),
    ('usr/bin/battery_monitor', None, 'monitor or runtime library'),
    ('usr/lib/libTelematics.so', None, 'monitor or runtime library'),
    ('usr/lib/systemd/system/battery_monitor.service', b'[Service]\nExecStart=/bin/true\n', 'invoke the retained monitor'),
])
def test_actual_boot_graph_must_be_headless_with_inactive_unconfigured_services_and_factory_protection(tmp_path, image, name, content, message):
    base, _, _ = make_neutral(tmp_path, image)
    rewrite_member(base, name, content)
    with pytest.raises(ValueError, match=message):
        verify_neutral(base)
