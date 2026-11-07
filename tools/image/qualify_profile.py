#!/usr/bin/env python3
"""Pin one locally qualified kernel as an explicit candidate image policy.

This records a new build identity. It neither changes the maintained release
profile nor grants redistribution rights or proves physical board operation.
"""
import argparse
import json
from pathlib import Path

from build_base import load_profile, source_identities
from image_io import write_json
from qualified_inputs import document, validate_vendor, validate_kernel, validate_sdk


def qualify(vendor_root, vendor_manifest, kernel_root, kernel_receipt, sdk, output,
            profile='tch-g62h-imx91-rel3.3'):
    profile_root = load_profile(profile)
    vendor = validate_vendor(vendor_root, vendor_manifest)
    kernel = validate_kernel(kernel_root, kernel_receipt, vendor, source_identities())
    sdk_record = validate_sdk(sdk, profile_root / 'dotnet-sdk.lock.json', profile_root / 'global.json', {})
    policy = document(profile_root / 'image-policy.json', 'Maintained image policy')
    if sdk_record['tree_sha256'] != policy['sdk_tree_sha256']:
        raise ValueError('Candidate qualification cannot silently change the pinned SDK')
    policy.update(kernel_release=kernel['manifest']['release'],
                  kernel_manifest_sha256=kernel['manifest_sha256'],
                  kernel_outputs=kernel['manifest']['outputs'], physical_acceptance='NOT_RUN')
    write_json(output, policy)
    return policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('vendor-root', 'vendor-manifest', 'kernel-root', 'kernel-receipt', 'sdk', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = qualify(args.vendor_root, args.vendor_manifest, args.kernel_root, args.kernel_receipt, args.sdk, args.output)
    print(json.dumps({'status': 'PINNED_LOCAL_CANDIDATE', 'kernel_release': result['kernel_release'],
                      'physical_acceptance': 'NOT_RUN'}))


if __name__ == '__main__':
    main()
