"""Explicit TCH neutralisation policy, not a general credential scanner."""
import hashlib
import re
from pathlib import Path
import tarfile
from image_io import archive, normalized, write_json

PROFILE_ID = 'tch-g62h-imx91-rel3.3'
HOSTNAME = b'iwave-tch\n'
# These contain provisioning, mutable identity or vendor demonstration payloads.
REMOVE_TREES = ('root', 'home', 'etc/ppp/peers', 'etc/NetworkManager/system-connections',
    'var/lib/NetworkManager', 'var/lib/connman', 'var/lib/bluetooth', 'var/lib/systemd/random-seed',
    'var/lib/dbus/machine-id', 'etc/wpa_supplicant', 'etc/connman', 'opt/ltp')
REMOVE_FILES = ('etc/ppp/ppp_on_boot', 'etc/ppp/quectel-pppd.sh', 'etc/ppp/quectel-ppp-kill',
    'etc/ppp/pap-secrets', 'etc/ppp/chap-secrets', 'etc/ppp/pap-secrets.example',
    'etc/ppp/chap-secrets.example', 'etc/ppp/eaptls-client.example', 'etc/ppp/eaptls-server.example',
    'etc/ppp/openssl.cnf.example', 'etc/ppp/options.example')
FORBIDDEN_TREES = ('etc/omam', 'opt/omam', 'var/lib/omam', 'usr/share/omam')
ACCOUNTS = ('etc/shadow', 'etc/gshadow')
HEADLESS_TARGET = '/usr/lib/systemd/system/multi-user.target'
MASK_UNITS = ('weston.service', 'weston.socket', 'assm_conf.service')
FACTORY_ENABLEMENT = 'etc/systemd/system/multi-user.target.wants/battery_monitor.service'
FACTORY_UNIT = 'usr/lib/systemd/system/battery_monitor.service'
FACTORY_FILES = (FACTORY_UNIT, 'usr/bin/battery_monitor', 'usr/lib/libTelematics.so')


def disabled_enablement(name):
    parts = name.split('/')
    return (name.startswith(('etc/systemd/system/', 'usr/lib/systemd/system/'))
            and len(parts) >= 2 and parts[-1] in MASK_UNITS
            and parts[-2].endswith(('.wants', '.requires')))



def under(name, roots):
    return any(name == root or name.startswith(root + '/') for root in roots)


def prohibited_path(name):
    if under(name, FORBIDDEN_TREES):
        return True
    if name.startswith(('etc/', 'usr/lib/systemd/', 'lib/systemd/')):
        return any(part.startswith('omam-') for part in name.split('/'))
    return False


def remove_path(name):
    return (under(name, REMOVE_TREES) or name in REMOVE_FILES or disabled_enablement(name)
            or name.startswith('etc/ssh/ssh_host_')
            or name.endswith('/authorized_keys') or name.endswith('/authorized_keys2'))


def lock_accounts(content):
    lines = []
    for line in content.decode('utf-8').splitlines():
        fields = line.split(':')
        if len(fields) < 2:
            raise ValueError('Malformed stock account database')
        fields[1] = '!'
        lines.append(':'.join(fields))
    return ('\n'.join(lines) + '\n').encode()


def stage_neutral(rootfs, overlay, kernel):
    """Read only named account/config files and enumerate exact stock mutations."""
    from qualified_inputs import stage_kernel
    overlay = Path(overlay)
    overlay.mkdir()
    vendor_types, links, replacements = {}, {}, {}
    deletes = set()
    required = {'etc/hostname', 'etc/machine-id', *ACCOUNTS}
    with archive(rootfs) as stream:
        for member in stream:
            name = normalized(member.name)
            if name in vendor_types:
                raise ValueError('Duplicate stock rootfs member')
            vendor_types[name] = member.type
            if prohibited_path(name):
                raise ValueError('Stock archive contains an application configuration path: ' + name)
            if member.islnk():
                links[name] = normalized(member.linkname)
            if remove_path(name):
                deletes.add(name)
            if name in required:
                if not member.isfile() or member.size > 1024 * 1024:
                    raise ValueError('Invalid stock neutralisation input: ' + name)
                data = stream.extractfile(member).read()
                replacements[name] = (HOSTNAME if name == 'etc/hostname' else
                    b'' if name == 'etc/machine-id' else lock_accounts(data), member.mode & 0o777)
    if set(replacements) != required:
        raise ValueError('Stock archive is missing required identity/account paths')
    # Reject retained aliases into a removed subtree. Generic composition also checks hardlinks.
    for name, target in links.items():
        if name not in deletes and target in deletes:
            raise ValueError('Retained stock hardlink references removed configuration: ' + name)
    deletes |= stage_kernel(kernel, overlay, vendor_types)
    for name, (data, mode) in replacements.items():
        path = overlay / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
    boot_links = {'etc/systemd/system/default.target': HEADLESS_TARGET,
        **{'etc/systemd/system/' + unit: '/dev/null' for unit in MASK_UNITS}}
    for name, target in boot_links.items():
        path = overlay / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
    planned_replacements = set(replacements) | (set(boot_links) & vendor_types.keys())
    plan = overlay.parent / 'neutral-overlay.json'
    write_json(plan, {'schema_version': 1, 'delete': sorted(deletes), 'replace': sorted(planned_replacements)})
    return plan, {'profile': PROFILE_ID, 'deleted_entries': len(deletes),
        'replaced_paths': sorted(planned_replacements), 'boot_target': 'multi-user.target',
        'masked_units': list(MASK_UNITS), 'accounts': 'all password fields locked',
        'cellular': 'no configured connection or APN; provision separately',
        'factory_monitor': 'stock protection retained; no automatic application power policy'}


def verify_neutral(rootfs):
    """Independently inspect actual archive paths and scoped provisioning content."""
    names, contents, violations, links, kinds = set(), {}, set(), {}, {}
    with archive(rootfs) as stream:
        for member in stream:
            name = normalized(member.name)
            if name in names:
                raise ValueError('Duplicate candidate archive entry')
            names.add(name)
            kinds[name] = member.type
            if member.issym():
                links[name] = member.linkname
            if member.isfile() and name == FACTORY_UNIT:
                if member.size > 64 * 1024:
                    raise ValueError('Factory monitor unit is unexpectedly large')
                contents[name] = stream.extractfile(member).read()
            if prohibited_path(name) or remove_path(name):
                violations.add(name)
            if member.isfile() and name.startswith('etc/') and member.size <= 2 * 1024**2:
                data = stream.extractfile(member).read()
                configured_cellular = re.search(rb'(?im)^\s*(?:apn|pin)\s*[:=]|[\"\'](?:apn|pin)[\"\']\s*:|\+cgdcont\s*=|<apn(?:\s|>)', data)
                if configured_cellular or b'/opt/omam' in data or b'/etc/omam' in data:
                    violations.add(name)
                if name in (*ACCOUNTS, 'etc/hostname', 'etc/machine-id'):
                    contents[name] = data
    if violations:
        raise ValueError('Neutral image contains forbidden provisioning/application paths: ' + ', '.join(sorted(violations)))
    if contents.get('etc/hostname') != HOSTNAME or contents.get('etc/machine-id') != b'':
        raise ValueError('Neutral hostname or empty machine identity is missing')
    for name in ACCOUNTS:
        if name not in contents or lock_accounts(contents[name]) != contents[name]:
            raise ValueError('Neutral image contains an unlocked account password: ' + name)
    if links.get('etc/systemd/system/default.target') != HEADLESS_TARGET:
        raise ValueError('Neutral image must boot the explicit headless multi-user target')
    for unit in MASK_UNITS:
        if links.get('etc/systemd/system/' + unit) != '/dev/null':
            raise ValueError('Neutral image is missing a required unconfigured-service mask: ' + unit)
    if links.get(FACTORY_ENABLEMENT) != '/' + FACTORY_UNIT:
        raise ValueError('Neutral image must keep factory battery monitor enabled')
    if any(kinds.get(name) not in (tarfile.REGTYPE, tarfile.AREGTYPE) for name in FACTORY_FILES):
        raise ValueError('Neutral image is missing the factory battery monitor or runtime library')
    if not re.search(rb'(?m)^ExecStart=/usr/bin/battery_monitor[ \t]*$', contents.get(FACTORY_UNIT, b'')):
        raise ValueError('Neutral image factory monitor unit must invoke the retained monitor')
    if kinds.get('etc/systemd/system/battery_monitor.service') is not None:
        raise ValueError('Neutral image cannot override the factory battery monitor unit')
    if 'opt/dotnet/dotnet' not in names or 'etc/profile.d/dotnet.sh' not in names:
        raise ValueError('Neutral image is missing its SDK or neutral shell profile')
    return {'status': 'PASS_SCOPED_ARCHIVE_CONTENT', 'profile': PROFILE_ID,
        'entries': len(names), 'machine_identity': 'empty', 'accounts': 'locked',
        'application': 'absent', 'cellular': 'unconfigured',
        'boot_target': 'multi-user.target', 'masked_units': list(MASK_UNITS),
        'factory_monitor': 'enabled with retained runtime',
        'scope': 'explicit profile paths and etc text; immutable pinned stock input, not a generic secret scan'}


def verify_base_payload(rootfs, policy):
    """Reconstruct the actual SDK fingerprint and compare every qualified module in the tar."""
    import json
    from verify_composed_rootfs import archive_entries, content_signature
    entries = archive_entries(Path(rootfs))
    sdk_hash = hashlib.sha256()
    for name, record in sorted(entries.items()):
        if not name.startswith('opt/dotnet/') or name == 'opt/dotnet/sdk-payload-report.json':
            continue
        item = {'path': name.removeprefix('opt/dotnet/'), 'mode': record['mode']}
        if record['type'] in (tarfile.REGTYPE.hex(), tarfile.AREGTYPE.hex(), tarfile.LNKTYPE.hex()):
            item.update(type='file', sha256=content_signature(name, entries)[1])
        elif record['type'] == tarfile.DIRTYPE.hex():
            item.update(type='directory')
        elif record['type'] == tarfile.SYMTYPE.hex():
            item.update(type='symlink', target=record['linkname'])
        else:
            raise ValueError('Unsupported SDK archive entry')
        sdk_hash.update((json.dumps(item, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii'))
    if sdk_hash.hexdigest() != policy['sdk_tree_sha256']:
        raise ValueError('Neutral base actual SDK bytes differ from the qualified profile')
    manifest_name = 'usr/share/iwave-linux-extensions/kernel/' + policy['kernel_release'] + '/kernel-manifest.json'
    if entries.get(manifest_name, {}).get('sha256') != policy['kernel_manifest_sha256']:
        raise ValueError('Neutral base actual kernel manifest differs from the qualified profile')
    kernel = None
    with archive(rootfs) as stream:
        for member in stream:
            if normalized(member.name) == manifest_name:
                if not member.isfile() or member.size > 8 * 1024**2:
                    raise ValueError('Invalid embedded kernel manifest')
                kernel = json.loads(stream.extractfile(member).read())
    if not isinstance(kernel, dict) or kernel.get('release') != policy['kernel_release']:
        raise ValueError('Neutral base embedded kernel release differs')
    expected = {**kernel['modules'], **kernel['module_metadata']}
    actual = {n.removeprefix('usr/') for n, r in entries.items()
        if n.startswith('usr/lib/modules/') and r['type'] != tarfile.DIRTYPE.hex()}
    if actual != set(expected):
        raise ValueError('Neutral base actual kernel modules or indexes differ')
    for name, declaration in expected.items():
        record = entries['usr/' + name]
        if record.get('sha256') != declaration['sha256'] or record['size'] != declaration['bytes']:
            raise ValueError('Neutral base actual kernel module bytes differ: ' + name)
    return {'status': 'PASS_ACTUAL_SDK_AND_KERNEL_BYTES', 'sdk_tree_sha256': sdk_hash.hexdigest(),
        'kernel_manifest_sha256': policy['kernel_manifest_sha256'], 'kernel_files': len(expected)}
