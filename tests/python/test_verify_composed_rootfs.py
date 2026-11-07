"""Verify complete synthetic rootfs contents and reject deliberate archive mutations."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


DIRECTORY = Path(__file__).resolve().parents[2] / "tools/image"
SPEC = importlib.util.spec_from_file_location("verify_composed_rootfs", DIRECTORY / "verify_composed_rootfs.py")
verifier = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(DIRECTORY), *sys.path]):
    SPEC.loader.exec_module(verifier)
    from compose_rootfs import compose


@unittest.skipUnless(shutil.which("zstd"), "zstd is required")
class VerifyComposedRootfsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sdk = self.root / "sdk"
        self.sdk.mkdir()
        (self.sdk / "dotnet").write_bytes(b"sdk host")
        (self.sdk / "dotnet").chmod(0o755)
        (self.sdk / "sdk-payload-report.json").write_text(json.dumps({
            "schema_version": 1, "rid": "linux-arm64", "tree_sha256": verifier.tree_sha256(self.sdk)}))
        self.vendor, self.candidate = self.root / "vendor.zst", self.root / "candidate.zst"
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
            member = tarfile.TarInfo("./vendor.conf")
            member.size, member.mode, member.uid, member.gid = 6, 0o640, 21, 22
            member.pax_headers = {"SCHILY.xattr.user.vendor": "preserve"}
            archive.addfile(member, io.BytesIO(b"vendor"))
        self.write_archive(self.vendor, raw.getvalue())
        compose(self.vendor, hashlib.sha256(self.vendor.read_bytes()).hexdigest(), self.sdk, self.candidate)

    def write_archive(self, path, raw):
        path.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=raw, capture_output=True, check=True, timeout=120).stdout)

    def mutate(self, target, change):
        raw = subprocess.run(["zstd", "-q", "-d", "-c", str(self.candidate)], capture_output=True, check=True, timeout=120).stdout
        output = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as source:
            with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for member in source:
                    content = source.extractfile(member).read() if member.isfile() else None
                    if verifier.normalized_name(member.name) == target:
                        if change == "omit":
                            continue
                        if change == "content":
                            content = b"x" * len(content)
                        elif change == "mode":
                            member.mode ^= 0o010
                        elif change == "owner":
                            member.uid += 1
                        elif change == "owner-name":
                            member.uname = "unexpected-account"
                        elif change == "sticky":
                            member.mode &= ~0o1000
                        elif change == "pax":
                            member.pax_headers["SCHILY.xattr.user.vendor"] = "changed"
                        elif change == "duplicate":
                            archive.addfile(member, io.BytesIO(content) if content is not None else None)
                    archive.addfile(member, io.BytesIO(content) if content is not None else None)
        self.write_archive(self.candidate, output.getvalue())

    def test_complete_candidate_preserves_every_vendor_entry(self):
        report = verifier.verify(self.vendor, self.candidate, self.sdk)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["vendor_entries"], 1)
        self.assertEqual(report["sdk_entries"], 2)
        self.assertEqual(report["candidate_rootfs_sha256"], hashlib.sha256(self.candidate.read_bytes()).hexdigest())
        self.assertNotIn("preserve", json.dumps(report))

    def test_vendor_content_mode_pax_and_missing_file_are_rejected(self):
        original = self.candidate.read_bytes()
        for change in ("content", "mode", "pax", "omit"):
            with self.subTest(change=change):
                self.candidate.write_bytes(original)
                self.mutate("vendor.conf", change)
                with self.assertRaisesRegex(ValueError, "Vendor entry changed or missing"):
                    verifier.verify(self.vendor, self.candidate, self.sdk)

    def test_sdk_archive_tampering_and_missing_file_are_rejected(self):
        original = self.candidate.read_bytes()
        for change in ("content", "mode", "omit"):
            with self.subTest(change=change):
                self.candidate.write_bytes(original)
                self.mutate("opt/dotnet/dotnet", change)
                with self.assertRaises(ValueError):
                    verifier.verify(self.vendor, self.candidate, self.sdk)

    def test_changed_local_sdk_fingerprint_is_rejected(self):
        (self.sdk / "dotnet").write_bytes(b"changed local host")
        with self.assertRaisesRegex(ValueError, "SDK tree fingerprint"):
            verifier.verify(self.vendor, self.candidate, self.sdk)

    def test_duplicate_normalized_path_is_rejected(self):
        self.mutate("vendor.conf", "duplicate")
        with self.assertRaisesRegex(ValueError, "Duplicate archive entry"):
            verifier.verify(self.vendor, self.candidate, self.sdk)

    def test_truncated_zstd_fails_verification(self):
        self.candidate.write_bytes(self.candidate.read_bytes()[:-1])
        with self.assertRaises((ValueError, tarfile.TarError)):
            verifier.verify(self.vendor, self.candidate, self.sdk)

    def compose_overlay(self, delete=False):
        overlay, plan = self.root / "overlay", self.root / "overlay-plan.json"
        overlay.mkdir()
        if not delete:
            (overlay / "vendor.conf").write_bytes(b"synthetic private replacement")
            (overlay / "vendor.conf").chmod(0o600)
        (overlay / "new.target").symlink_to("/usr/lib/systemd/system/multi-user.target")
        plan.write_text(json.dumps({"schema_version": 1, "delete": ["vendor.conf"] if delete else [],
                                    "replace": [] if delete else ["vendor.conf"]}))
        self.candidate.unlink()
        compose(self.vendor, hashlib.sha256(self.vendor.read_bytes()).hexdigest(), self.sdk,
                self.candidate, overlay_dir=overlay, overlay_plan=plan)
        return overlay, plan

    def test_planned_overlay_is_verified_without_exposing_content(self):
        overlay, plan = self.compose_overlay()
        result = verifier.verify(self.vendor, self.candidate, self.sdk, overlay, plan)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["overlay"]["replaced_entries"], 1)
        self.assertNotIn("synthetic private replacement", json.dumps(result))
        self.mutate("vendor.conf", "content")
        with self.assertRaisesRegex(ValueError, "Overlay file content differs"):
            verifier.verify(self.vendor, self.candidate, self.sdk, overlay, plan)

    def test_overlay_deletion_is_exact_and_unplanned_changes_are_rejected(self):
        overlay, plan = self.compose_overlay(delete=True)
        result = verifier.verify(self.vendor, self.candidate, self.sdk, overlay, plan)
        self.assertEqual(result["overlay"]["deleted_entries"], 1)
        with self.assertRaisesRegex(ValueError, "Vendor entry changed or missing"):
            verifier.verify(self.vendor, self.candidate, self.sdk)

    def test_installed_overlay_owner_and_sticky_bit_are_verified(self):
        overlay, plan = self.root / "installed", self.root / "installed-plan.json"
        state = overlay / "var/lib/omam"
        state.mkdir(parents=True)
        state.chmod(0o1770)
        plan.write_text(json.dumps({"schema_version": 1, "delete": [], "replace": []}))
        self.candidate.unlink()
        compose(self.vendor, hashlib.sha256(self.vendor.read_bytes()).hexdigest(), self.sdk,
                self.candidate, overlay_dir=overlay, overlay_plan=plan, preserve_overlay_ownership=True)
        result = verifier.verify(self.vendor, self.candidate, self.sdk, overlay, plan,
                                 preserve_overlay_ownership=True)
        self.assertEqual(result["overlay"]["ownership"], "staged-numeric-ids")
        with self.assertRaisesRegex(ValueError, "Injected metadata differs"):
            verifier.verify(self.vendor, self.candidate, self.sdk, overlay, plan)
        original = self.candidate.read_bytes()
        for change in ("owner", "owner-name", "sticky"):
            with self.subTest(change=change):
                self.candidate.write_bytes(original)
                self.mutate("var/lib/omam", change)
                with self.assertRaisesRegex(ValueError, "Injected metadata differs"):
                    verifier.verify(self.vendor, self.candidate, self.sdk, overlay, plan,
                                    preserve_overlay_ownership=True)

    def test_preserved_ownership_requires_an_explicit_overlay(self):
        with self.assertRaisesRegex(ValueError, "explicitly staged overlay"):
            verifier.verify(self.vendor, self.candidate, self.sdk, preserve_overlay_ownership=True)


if __name__ == "__main__":
    unittest.main()
