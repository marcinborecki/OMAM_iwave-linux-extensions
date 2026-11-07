#!/usr/bin/env python3
"""Independently verify a neutral archive's exact hash and profile-scoped privacy rules."""
import argparse
import json
from pathlib import Path
import re
import sys
from image_io import digest
from neutral_profile import PROFILE_ID, verify_neutral


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--profile', required=True)
    args = parser.parse_args()
    try:
        if args.profile != PROFILE_ID:
            raise ValueError('Unknown or unqualified hardware profile')
        if not re.fullmatch('[a-f0-9]{64}', args.sha256) or digest(args.rootfs) != args.sha256:
            raise ValueError('Neutral rootfs SHA256 differs')
        print(json.dumps(verify_neutral(args.rootfs), indent=2))
    except (OSError, ValueError) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
