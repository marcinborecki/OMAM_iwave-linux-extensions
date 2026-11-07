#!/usr/bin/env python3
"""Three bounded patch delivery checks against a tiny explicit vendor fixture."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import sys

sys.dont_write_bytecode = True
import unittest

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
args = parser.parse_args()
MANIFEST = json.loads((ROOT / "components/flexcan-controller-policy/manifest.json").read_text())
spec = importlib.util.spec_from_file_location("iwave_kernel_apply", ROOT / "tools/kernel/apply.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BASE = {}
for name, expected in MANIFEST["base_sha256"].items():
    data = subprocess.run(["git", "-C", str(args.source), "show", MANIFEST["srcrev"] + ":" + name],
                          capture_output=True, check=True, timeout=20).stdout
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit("Vendor fixture identity mismatch: " + name)
    BASE[name] = data


class PatchDelivery(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="omam-flexcan-patch-test-")
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name)
        for name, data in BASE.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_application_and_complete_identity(self):
        self.assertTrue(module.apply(self.source).startswith("applied:"))
        for name, expected in MANIFEST["result_sha256"].items():
            self.assertEqual(module.digest(self.source / name), expected)
        self.assertTrue(module.apply(self.source, check_only=True).startswith("verified:"))

    def test_exact_result_is_idempotent(self):
        module.apply(self.source)
        before = {name: (self.source / name).stat().st_mtime_ns for name in MANIFEST["result_sha256"]}
        module.apply(self.source)
        self.assertEqual(before, {name: (self.source / name).stat().st_mtime_ns for name in before})

    def test_drift_is_refused_before_any_write(self):
        changed = self.source / "drivers/net/can/flexcan/Makefile"
        changed.write_text(changed.read_text() + "# unrelated owner edit\n")
        before = {name: (self.source / name).read_bytes() for name in BASE}
        with self.assertRaisesRegex(ValueError, "changed base"):
            module.apply(self.source)
        self.assertEqual(before, {name: (self.source / name).read_bytes() for name in BASE})
        self.assertFalse((self.source / "include/uapi/linux/omam_flexcan.h").exists())


unittest.main(argv=[__file__], verbosity=2)
