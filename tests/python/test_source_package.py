"""Actual tiny Git working trees and streaming zstd archives, without vendor assets."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location('source_package', Path(__file__).resolve().parents[2] / 'tools/source/package.py')
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def save_json(path, value):
    return save(path, (json.dumps(value, sort_keys=True) + '\n').encode())


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True, timeout=30).stdout


@pytest.fixture
def fixture(tmp_path):
    source, public, kernel = (tmp_path / n for n in ('linux', 'public', 'kernel'))
    source.mkdir()
    git(source, 'init', '-q')
    save(source / 'COPYING', b'fixture Linux licensing notice\n')
    save(source / '.gitignore', b'ignored/\n*.o\n.config\n')
    save(source / 'driver.c', b'old source\n')
    save(source / 'removed.c', b'deleted source\n')
    save(source / 'tools/testing/fixture.bin', b'\x00\xfftracked binary source fixture')
    save(source / 'build.sh', b'#!/bin/sh\nexit 0\n')
    (source / 'build.sh').chmod(0o755)
    (source / 'driver-link.c').symlink_to('driver.c')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Source Fixture', '-c', 'user.email=source-fixture@example.invalid', 'commit', '-qm', 'Fixture source')
    save(source / 'driver.c', b'actual vendor and extension changes\n')
    save(source / 'added-board.dts', b'// vendor-added Device Tree\n')
    (source / 'removed.c').unlink()
    save(source / 'ignored/private-input', b'ignored data must not be packaged')
    save(source / 'generated.o', b'compiled bytes must not be packaged')
    save(source / '.git/private-sentinel', b'Git metadata must not be packaged')
    for name in ('components/patch.diff', 'profiles/' + subject.PROFILE + '/source-gaps.json',
                 'tools/kernel/qualify.py', 'tests/fixture.c', 'LICENSES/GPL-2.0.txt',
                 'LICENSE', 'NOTICE', 'README.md', 'docs/source-delivery.md'):
        save(public / name, ('public fixture ' + name).encode())
    save_json(public / 'profiles' / subject.PROFILE / 'source-gaps.json', {'external_modules': [
        {'module': name} for name in ('cryptodev.ko', 'igb_uio.ko', 'kpage_ncache.ko', 'mlan.ko', 'moal.ko')]})
    save(public / '.local/private-input', b'private project input')
    _, source_identity = subject.source_inventory(source)
    config = save(kernel / 'config', b'CONFIG_TEST=y\n')
    external = {'lib/modules/test/updates/' + name: {'bytes': 1, 'sha256': hashlib.sha256(name.encode()).hexdigest()}
        for name in ('cryptodev.ko', 'igb_uio.ko', 'kpage_ncache.ko', 'mlan.ko', 'moal.ko')}
    manifest = {'release': 'test-kernel', 'static_qualification': 'PASS_BUILD_AND_STATIC_ABI',
        'inputs': {'srcrev': source_identity['revision'], 'source_diff_sha256': source_identity['diff_sha256'],
            'source_added_files_sha256': source_identity['added_files_sha256']},
        'outputs': {'config': config}, 'modules': external, 'module_metadata': {},
        'stock_external_provenance': {'files': external}}
    declaration = save_json(kernel / 'kernel-manifest.json', manifest)
    save_json(public / 'profiles' / subject.PROFILE / 'image-policy.json',
        {'kernel_manifest_sha256': declaration['sha256'], 'kernel_release': manifest['release']})
    return source, public, kernel, tmp_path / 'release.tar.zst'


def archive(path):
    decoded = subprocess.run(['zstd', '-qdc', str(path)], capture_output=True, check=True, timeout=30).stdout
    return tarfile.open(fileobj=io.BytesIO(decoded), mode='r:')


def test_actual_modified_added_and_deleted_sources_are_exported_with_portable_build_inputs(fixture):
    source, public, kernel, output = fixture
    result = subject.package(source, public, kernel, output, max_output_bytes=1024**2, source_date_epoch=7)
    assert result['status'] == 'PASS_SOURCE_ONLY_PACKAGE'
    assert result['completeness'] == 'INCOMPLETE_FOR_FIVE_RETAINED_FACTORY_MODULES'
    with archive(output) as tar:
        prefix = 'iwave-linux-source/'
        entries = {m.name: m for m in tar}
        assert tar.extractfile(prefix + 'linux/driver.c').read() == b'actual vendor and extension changes\n'
        assert tar.extractfile(prefix + 'linux/added-board.dts').read() == b'// vendor-added Device Tree\n'
        assert prefix + 'linux/removed.c' not in entries
        assert tar.extractfile(prefix + 'linux/tools/testing/fixture.bin').read() == b'\x00\xfftracked binary source fixture'
        assert not any('/.git/' in n or '/ignored/' in n or '/.local/' in n or n.endswith('.o') for n in entries)
        link = entries[prefix + 'linux/driver-link.c']
        assert link.issym() and link.linkname == 'driver.c'
        executable = entries[prefix + 'linux/build.sh']
        assert (executable.mode, executable.uid, executable.gid, executable.mtime) == (0o755, 0, 0, 7)
        assert tar.extractfile(prefix + 'qualification/kernel.config').read() == b'CONFIG_TEST=y\n'
        manifest = json.load(tar.extractfile(prefix + 'source-manifest.json'))
        assert manifest['excluded_external_modules'] == ['cryptodev.ko', 'igb_uio.ko', 'kpage_ncache.ko', 'mlan.ko', 'moal.ko']
        assert manifest['source']['deleted_tracked_files'] == ['removed.c']
        assert manifest['source_gap_reference'].endswith('/source-gaps.json')
        for name, record in manifest['files'].items():
            member = entries[prefix + name]
            assert member.mode == record['mode']
            if record['type'] == 'file':
                assert hashlib.sha256(tar.extractfile(member).read()).hexdigest() == record['sha256']
            else:
                assert member.linkname == record['target']
        assert not any(n.endswith('.ko') for n in entries)


def test_qualified_source_mismatch_is_rejected_before_output(fixture):
    source, public, kernel, output = fixture
    save(source / 'driver.c', b'unqualified change\n')
    with pytest.raises(ValueError, match='differs from the qualified'):
        subject.package(source, public, kernel, output, max_output_bytes=1024**2)
    assert not output.exists()
    assert not list(output.parent.glob('.kernel-source-*'))


def test_generated_and_private_nonignored_additions_are_not_read_or_published(fixture):
    source, public, kernel, output = fixture
    save(source / '.local/private-input', b'private file excluded by explicit local-input path')
    files, _ = subject.source_inventory(source)
    assert '.local/private-input' not in files
    assert 'generated.o' not in files
    assert all(not n.startswith('.git/') for n in files)


def test_source_symlink_cannot_export_a_file_outside_source_root(fixture):
    source, public, kernel, output = fixture
    (source / 'driver-link.c').unlink()
    (source / 'driver-link.c').symlink_to('../outside-source')
    with pytest.raises(ValueError, match='escapes'):
        subject.package(source, public, kernel, output, max_output_bytes=1024**2)
    assert not output.exists()


def test_compressor_failure_cleans_its_partial_archive_and_process(fixture, monkeypatch):
    source, public, kernel, output = fixture
    original = subprocess.Popen
    children = []
    def failing(command, *args, **kwargs):
        if 'zstd' in command:
            command = [sys.executable, '-c', 'import sys; sys.stdin.buffer.read(); sys.exit(23)']
            child = original(command, *args, **kwargs)
            children.append(child)
            return child
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', failing)
    with pytest.raises(ValueError, match='compression failed'):
        subject.package(source, public, kernel, output, max_output_bytes=1024**2)
    assert children and all(p.poll() is not None for p in children)
    assert not output.exists()
    assert not list(output.parent.glob('.kernel-source-*'))


def test_output_size_bound_is_enforced_without_publishing_partial_bytes(fixture):
    source, public, kernel, output = fixture
    with pytest.raises((OSError, ValueError)):
        subject.package(source, public, kernel, output, max_output_bytes=16)
    assert not output.exists()
    assert not list(output.parent.glob('.kernel-source-*'))


def test_existing_owner_output_is_never_overwritten(fixture):
    source, public, kernel, output = fixture
    output.write_bytes(b'owner artifact')
    with pytest.raises(ValueError, match='absent'):
        subject.package(source, public, kernel, output, max_output_bytes=1024**2)
    assert output.read_bytes() == b'owner artifact'



def test_dry_run_validates_actual_sources_without_creating_output_or_partial_archive(fixture):
    source, public, kernel, output = fixture
    result = subject.package(source, public, kernel, output, max_output_bytes=1024**2, dry_run=True)
    assert result['status'] == 'VALIDATED_SOURCE_INPUTS_ONLY'
    assert result['archive'] == 'NOT_CREATED'
    assert result['source_files'] > 0
    assert not output.exists()
    assert not list(output.parent.glob('.kernel-source-*'))
