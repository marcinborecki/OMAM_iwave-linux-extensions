#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../../.." && pwd)
work=$(mktemp -d -t omam-flexcan-tests-XXXXXXXX)
trap 'rm -rf "$work"' EXIT HUP INT TERM
# Small standalone policy/transaction tests; no kernel build or controller access.
timeout --kill-after=2s 60s cc -D_DEFAULT_SOURCE -std=c11 -O2 -g -Wall -Wextra -Werror \
  -fsanitize=undefined -fno-sanitize-recover=all \
  -I"$root/components/flexcan-controller-policy/include/uapi" -I"$root/components/flexcan-controller-policy" \
  "$root/tests/native/flexcan/policy_test.c" -o "$work/policy_test"
timeout --kill-after=2s 20s "$work/policy_test"
