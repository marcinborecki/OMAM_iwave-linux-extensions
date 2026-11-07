#!/usr/bin/env python3
"""Build a local neutral TCH image from pinned stock inputs and a qualified kernel."""
import argparse
import json
from pathlib import Path
import shutil
import signal
import sys
import tarfile
import tempfile

from compose_rootfs import compose
from image_io import digest, write_json
from neutral_profile import PROFILE_ID, stage_neutral, verify_neutral, verify_base_payload
from qualified_inputs import (VENDOR_FILES, checked_file, copy_boot_inputs, document,
    release_sources, validate_vendor, validate_kernel, validate_sdk)
from verify_composed_rootfs import verify

PROJECT = Path(__file__).resolve().parents[2]
RESERVE_BYTES = 500 * 1024**2


def load_profile(profile):
    if profile != PROFILE_ID:
        raise ValueError('Unknown or unqualified hardware profile')
    return PROJECT / 'profiles' / profile


def source_identities():
    return release_sources(PROJECT / 'components/bq25601-poweroff/source.json',
        PROJECT / 'components/bq25601-poweroff/bq25601-ship-on-poweroff.patch',
        PROJECT / 'components/flexcan-controller-policy/manifest.json',
        PROJECT / 'components/flexcan-controller-policy/include/uapi/linux/omam_flexcan.h')


def sizing(vendor_root, sdk, kernel_root, output_parent, scratch_root):
    """Conservative compressed-output bound plus staged modules, without creating files."""
    stock = Path(vendor_root) / 'Linux/rootfs.tar.zst'
    sdk_bytes = sum(p.stat().st_size for p in Path(sdk).rglob('*') if p.is_file() and not p.is_symlink())
    kernel_bytes = sum(p.stat().st_size for p in Path(kernel_root).rglob('*') if p.is_file() and not p.is_symlink())
    boot_bytes = sum((Path(vendor_root) / n).stat().st_size for n in VENDOR_FILES if n != 'Linux/rootfs.tar.zst')
    # Includes uncompressed SDK bytes and an extra stock-rootfs allowance for compression changes.
    output_bound = 2 * stock.stat().st_size + sdk_bytes + boot_bytes + kernel_bytes
    scratch_bound = kernel_bytes + 16 * 1024**2
    output_parent, scratch_root = Path(output_parent), Path(scratch_root)
    same_device = output_parent.stat().st_dev == scratch_root.stat().st_dev
    output_free, scratch_free = shutil.disk_usage(output_parent).free, shutil.disk_usage(scratch_root).free
    # Streaming compose temporarily keeps the complete candidate and its hardlink on one device,
    # not two copies; scratch only holds the small module/metadata overlay.
    output_required = output_bound + RESERVE_BYTES + (scratch_bound if same_device else 0)
    scratch_required = scratch_bound + RESERVE_BYTES
    if output_free < output_required or scratch_free < scratch_required:
        raise ValueError('Insufficient storage for conservative image outputs, overlay and 500 MiB reserve')
    return {'output_bound_bytes': output_bound, 'scratch_bound_bytes': scratch_bound,
        'output_free_bytes': output_free, 'scratch_free_bytes': scratch_free,
        'reserve_bytes': RESERVE_BYTES, 'same_filesystem': same_device}


def image_policy(profile, policy_path=None):
    """Select the default release pin or an explicitly trusted local candidate pin."""
    path = Path(policy_path) if policy_path is not None else profile / 'image-policy.json'
    policy = document(path, 'Image policy')
    if (policy.get('schema') != 'iwave.image-profile.v1' or policy.get('profile') != PROFILE_ID
            or policy.get('qualification') != 'PASS_BUILD_AND_STATIC_ABI'):
        raise ValueError('Image policy must identify the qualified hardware profile')
    return policy, path


def validate_base(root, expected_profile=PROFILE_ID, policy_path=None):
    """Private consumers must bind the complete base, never silently fall back to stock."""
    profile = load_profile(expected_profile)
    policy, policy_file = image_policy(profile, policy_path)
    root = Path(root)
    manifest = document(root / 'base-manifest.json', 'Neutral base manifest')
    if (manifest.get('schema') != 'iwave.linux-base.v1' or manifest.get('profile') != expected_profile
            or manifest.get('privacy', {}).get('status') != 'PASS_SCOPED_ARCHIVE_CONTENT'
            or manifest.get('verification', {}).get('status') != 'PASS'):
        raise ValueError('Neutral base profile or accepted content verification differs')
    bound_policy = manifest.get('image_policy_sha256')
    if ((bound_policy is not None or policy_path is not None)
            and bound_policy != digest(policy_file)):
        raise ValueError('Neutral base differs from the explicitly selected image policy')
    files = manifest.get('files')
    if not isinstance(files, dict) or set(files) != set(VENDOR_FILES):
        raise ValueError('Neutral base must bind every boot and rootfs file')
    for name, record in files.items():
        checked_file(root, name, record)
    kernel = manifest.get('kernel', {})
    sources = manifest.get('source_identities')
    if sources != source_identities() or kernel.get('static_qualification') != 'PASS_BUILD_AND_STATIC_ABI':
        raise ValueError('Neutral base kernel source identity differs from this profile')
    if kernel.get('outputs', {}).get('Image') != files['Linux/Image']:
        raise ValueError('Neutral base Image differs from its qualified kernel')
    dtb = 'imx91-iwg62h-prgtt.dtb'
    if kernel.get('outputs', {}).get(dtb) != files['Linux/' + dtb]:
        raise ValueError('Neutral base DTB differs from its qualified kernel')
    if (manifest.get("sdk", {}).get("tree_sha256") != policy["sdk_tree_sha256"]
            or kernel.get("manifest_sha256") != policy["kernel_manifest_sha256"]
            or kernel.get("outputs") != policy["kernel_outputs"]):
        raise ValueError("Neutral base SDK or kernel differs from the qualified image profile")
    verify_neutral(root / "Linux/rootfs.tar.zst")
    verify_base_payload(root / "Linux/rootfs.tar.zst", policy)
    return manifest


def build(args):
    profile = load_profile(args.profile)
    output = args.output
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Output must be a new directory with an existing parent')
    scratch_root = args.scratch_root or Path(tempfile.gettempdir())
    storage = sizing(args.vendor_root, args.sdk, args.kernel_root, output.parent, scratch_root)
    vendor = validate_vendor(args.vendor_root, args.vendor_manifest)
    sources = source_identities()
    kernel = validate_kernel(args.kernel_root, args.kernel_receipt, vendor, sources)
    sdk = validate_sdk(args.sdk, profile / 'dotnet-sdk.lock.json', profile / 'global.json', {})
    policy, policy_file = image_policy(profile, getattr(args, 'image_policy', None))
    if kernel["manifest_sha256"] != policy["kernel_manifest_sha256"] or sdk["tree_sha256"] != policy["sdk_tree_sha256"]:
        raise ValueError("Kernel or SDK does not match the qualified image profile")
    if args.dry_run:
        return {'status': 'VALIDATED_INPUTS_ONLY', 'profile': args.profile, 'storage': storage,
            'kernel_release': kernel['manifest']['release'], 'sdk': sdk,
            'composition': 'NOT_RUN', 'privacy': 'NOT_RUN', 'redistribution_status': 'BLOCKED'}
    output.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix='iwave-neutral-', dir=scratch_root) as temporary:
            work = Path(temporary)
            stock = args.vendor_root / 'Linux/rootfs.tar.zst'
            overlay = work / 'overlay'
            plan, neutralisation = stage_neutral(stock, overlay, kernel)
            copy_boot_inputs(args.vendor_root, vendor, kernel, output)
            target = output / 'Linux/rootfs.tar.zst'
            composition = compose(stock, vendor['files']['Linux/rootfs.tar.zst']['sha256'], args.sdk,
                target, args.source_date_epoch, overlay, plan, profile_path='etc/profile.d/dotnet.sh')
            verification = verify(stock, target, args.sdk, overlay, plan, profile_path='etc/profile.d/dotnet.sh')
            privacy = verify_neutral(target)
            files = {name: {'bytes': (output / name).stat().st_size, 'sha256': digest(output / name)} for name in VENDOR_FILES}
            manifest = {'schema': 'iwave.linux-base.v1', 'profile': args.profile,
                'image_policy_sha256': digest(policy_file),
                'files': files, 'source_date_epoch': args.source_date_epoch,
                'vendor': vendor, 'source_identities': sources,
                'kernel': {'release': kernel['manifest']['release'], 'manifest_sha256': kernel['manifest_sha256'],
                    'build_receipt_sha256': kernel['receipt_sha256'], 'outputs': kernel['manifest']['outputs'],
                    'static_qualification': kernel['manifest']['static_qualification'], 'counts': kernel['counts'],
                    'physical_loading': 'NOT_RUN', 'hardware_readback': 'NOT_RUN'},
                'sdk': sdk, 'composition': composition, 'verification': verification,
                'privacy': privacy, 'neutralisation': neutralisation,
                'redistribution_status': 'BLOCKED',
                'redistribution_gaps': ['Separate vendor library/firmware terms are not established from supplied material',
                    'Five preserved factory modules have candidate sources; actual vendor source correspondence remains unconfirmed',
                    'Full-image rootfs, boot and recovery source/notice coverage remains incomplete'],
                'redistribution_interpretation': 'Local release assessment is incomplete; unknown terms are not a confirmed prohibition'}
            write_json(output / 'base-manifest.json', manifest)
        return {'status': 'PASS_LOCAL_COMPOSITION', 'profile': args.profile,
            'manifest_sha256': digest(output / 'base-manifest.json'), 'files': files,
            'physical_acceptance': 'NOT_RUN', 'redistribution_status': 'BLOCKED'}
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    for name in ('vendor-root', 'vendor-manifest', 'kernel-root', 'kernel-receipt', 'sdk', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--scratch-root', type=Path)
    parser.add_argument('--image-policy', type=Path,
        help='Explicit local policy created after qualifying a new kernel; default release pins remain unchanged')
    parser.add_argument('--source-date-epoch', type=int, default=0)
    parser.add_argument('--dry-run', action='store_true', help='Validate pinned inputs and storage; never compose an image')
    args = parser.parse_args()
    def terminate(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        print(json.dumps(build(args), indent=2))
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Image composition interrupted; owned output and scratch removed', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
