"""Synthetic archive tests only: no vendor image, root access, or device is used."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


DIRECTORY = Path(__file__).resolve().parents[2] / "tools/image"
SPEC = importlib.util.spec_from_file_location("compose_rootfs", DIRECTORY / "compose_rootfs.py")
composer = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(DIRECTORY), *sys.path]):
    SPEC.loader.exec_module(composer)


@unittest.skipUnless(shutil.which("zstd"), "zstd is required for the streaming archive boundary")
class ComposeRootfsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sdk = self.root / "sdk"
        self.sdk.mkdir()
        (self.sdk / "dotnet").write_bytes(b"synthetic host")
        (self.sdk / "dotnet").chmod(0o755)
        self.write_sdk_report()
        self.vendor = self.root / "vendor.tar.zst"
        self.output = self.root / "custom.tar.zst"

    def write_sdk_report(self):
        (self.sdk / "sdk-payload-report.json").write_text(json.dumps({
            "schema_version": 1, "rid": "linux-arm64", "host_from_sdk": "11.0.100-rc.1",
            "tree_sha256": composer.tree_sha256(self.sdk)}))

    def fixture(self, extras=()):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name in ("./", "./etc", "./opt"):
                directory = tarfile.TarInfo(name)
                directory.type, directory.mode, directory.uid, directory.gid = tarfile.DIRTYPE, 0o750, 41, 42
                archive.addfile(directory)
            file = tarfile.TarInfo("./etc/vendor.conf")
            file.size, file.mode, file.uid, file.gid, file.mtime = 6, 0o640, 51, 52, 123
            file.pax_headers = {"SCHILY.xattr.user.vendor": "iwave", "SCHILY.acl.access": "user::rw-,group::r--,other::---"}
            archive.addfile(file, io.BytesIO(b"vendor"))
            for name, kind, target in (("etc/alias", tarfile.LNKTYPE, "./etc/vendor.conf"),
                                       ("vendor-link", tarfile.SYMTYPE, "/etc/vendor.conf")):
                link = tarfile.TarInfo(name)
                link.type, link.linkname = kind, target
                archive.addfile(link)
            for extra in extras:
                archive.addfile(extra, io.BytesIO(b"x" * extra.size))
        compressed = subprocess.run(["zstd", "-q", "-c"], input=raw.getvalue(), capture_output=True, check=True, timeout=120).stdout
        self.vendor.write_bytes(compressed)
        return hashlib.sha256(compressed).hexdigest()

    def read_output(self):
        decoded = subprocess.run(["zstd", "-q", "-d", "-c", str(self.output)], capture_output=True, check=True, timeout=120).stdout
        return tarfile.open(fileobj=io.BytesIO(decoded), mode="r:")

    def test_preserves_vendor_metadata_and_injects_root_owned_sdk(self):
        result = composer.compose(self.vendor, self.fixture(), self.sdk, self.output, 456)
        with self.read_output() as archive:
            members = {str(Path(member.name)): member for member in archive.getmembers()}
            original = members["etc/vendor.conf"]
            self.assertEqual((original.uid, original.gid, original.mode, original.mtime), (51, 52, 0o640, 123))
            self.assertEqual(archive.extractfile(original).read(), b"vendor")
            self.assertEqual(original.pax_headers["SCHILY.xattr.user.vendor"], "iwave")
            self.assertEqual(original.pax_headers["SCHILY.acl.access"], "user::rw-,group::r--,other::---")
            self.assertEqual((members["opt"].uid, members["opt"].mode), (41, 0o750))
            self.assertTrue(members["etc/alias"].islnk())
            self.assertEqual(members["etc/alias"].linkname, "./etc/vendor.conf")
            self.assertEqual(members["vendor-link"].linkname, "/etc/vendor.conf")
            sdk = members["opt/dotnet/dotnet"]
            self.assertEqual((sdk.uid, sdk.gid, sdk.mode, sdk.mtime), (0, 0, 0o755, 456))
            self.assertEqual(archive.extractfile(sdk).read(), b"synthetic host")
            self.assertEqual(members["usr/local/bin/dotnet"].linkname, "/opt/dotnet/dotnet")
            self.assertEqual(archive.extractfile(members["etc/profile.d/omam-dotnet.sh"]).read(), composer.PROFILE)
            self.assertEqual((members["usr/local"].uid, members["usr/local"].mode), (0, 0o755))
        self.assertEqual(result["output_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_collision_and_symlink_parent_leave_no_output(self):
        for name, kind in (("./opt/dotnet/old", tarfile.REGTYPE),
                           ("./etc/profile.d/omam-dotnet.sh", tarfile.REGTYPE),
                           ("usr/local/bin/dotnet", tarfile.SYMTYPE),
                           ("usr/local", tarfile.SYMTYPE), ("etc/profile.d", tarfile.REGTYPE)):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.type, member.linkname = kind, "/outside"
                pin = self.fixture([member])
                with self.assertRaises(ValueError):
                    composer.compose(self.vendor, pin, self.sdk, self.output)
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.root.glob(".rootfs-compose-*")))

    def test_installed_overlay_preserves_numeric_owners_and_directory_modes(self):
        overlay = self.root / "installed"
        directory = overlay / "var/lib/omam"
        directory.mkdir(parents=True)
        directory.chmod(0o1770)
        captures = directory / "captures"
        captures.mkdir(mode=0o2750)
        captures.chmod(0o2750)
        (directory / "public-state.json").write_bytes(b"synthetic state")
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"schema_version": 1, "delete": [], "replace": []}))
        original_lstat = Path.lstat

        def target_ids(path):
            info = original_lstat(path)
            if path in (directory, captures):
                values = list(info)
                values[4:6] = (3210, 3211)
                return os.stat_result(values)
            return info

        # Model the installed root's account IDs without requiring privileged test chown.
        with patch.object(Path, "lstat", target_ids):
            result = composer.compose(self.vendor, self.fixture(), self.sdk, self.output, 456,
                                      overlay, plan, preserve_overlay_ownership=True)
        with self.read_output() as archive:
            state = archive.getmember("var/lib/omam")
            self.assertEqual((state.uid, state.gid, state.uname, state.gname, state.mode),
                             (3210, 3211, "", "", 0o1770))
            capture = archive.getmember("var/lib/omam/captures")
            self.assertEqual((capture.uid, capture.gid, capture.mode), (3210, 3211, 0o2750))
            sdk = archive.getmember("opt/dotnet/dotnet")
            self.assertEqual((sdk.uid, sdk.gid, sdk.uname, sdk.gname, sdk.mode), (0, 0, "root", "root", 0o755))
        self.assertEqual(result["overlay"]["ownership"], "staged-numeric-ids")

    def test_installed_overlay_refuses_privileged_files_before_compression(self):
        overlay = self.root / "installed"
        overlay.mkdir()
        executable = overlay / "helper"
        executable.write_bytes(b"synthetic helper")
        executable.chmod(0o4755)
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"schema_version": 1, "delete": [], "replace": []}))
        pin = self.fixture()
        with patch.object(composer.subprocess, "Popen") as process:
            with self.assertRaisesRegex(ValueError, "only on directories"):
                composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay,
                                 overlay_plan=plan, preserve_overlay_ownership=True)
            process.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_preserved_ownership_requires_an_overlay(self):
        with self.assertRaisesRegex(ValueError, "explicitly staged overlay"):
            composer.compose(self.vendor, self.fixture(), self.sdk, self.output, preserve_overlay_ownership=True)
        self.assertFalse(self.output.exists())

    def test_bad_hash_fails_before_starting_compression(self):
        self.fixture()
        with patch.object(composer.subprocess, "Popen") as process:
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                composer.compose(self.vendor, "0" * 64, self.sdk, self.output)
            process.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_existing_output_is_immutable(self):
        self.output.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            composer.compose(self.vendor, self.fixture(), self.sdk, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep")

    def test_sdk_symlink_cannot_escape(self):
        (self.sdk / "bad-link").symlink_to("../outside")
        self.write_sdk_report()
        with self.assertRaisesRegex(ValueError, "SDK symlink escapes"):
            composer.compose(self.vendor, self.fixture(), self.sdk, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_sdk_fails_before_creating_output(self):
        pin = self.fixture()
        (self.sdk / "dotnet").write_bytes(b"modified host")
        with patch.object(composer.subprocess, "Popen") as process:
            with self.assertRaisesRegex(ValueError, "SDK tree SHA256 mismatch"):
                composer.compose(self.vendor, pin, self.sdk, self.output)
            process.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".rootfs-compose-*")))

    def test_empty_directory_added_by_sdk_smoke_is_detected(self):
        pin = self.fixture()
        # The real SDK smoke created this empty directory after staging.
        (self.sdk / "metadata").mkdir(mode=0o775)
        with self.assertRaisesRegex(ValueError, "SDK tree SHA256 mismatch"):
            composer.compose(self.vendor, pin, self.sdk, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".rootfs-compose-*")))

    def test_concurrent_output_creation_is_preserved_at_publication(self):
        pin, link = self.fixture(), composer.os.link
        def publish_after_competing_writer(source, destination):
            self.output.write_bytes(b"competing writer")
            link(source, destination)
        with patch.object(composer.os, "link", side_effect=publish_after_competing_writer):
            with self.assertRaises(FileExistsError):
                composer.compose(self.vendor, pin, self.sdk, self.output)
        self.assertEqual(self.output.read_bytes(), b"competing writer")
        self.assertFalse(list(self.root.glob(".rootfs-compose-*")))

    def test_sparse_and_traversal_members_are_rejected(self):
        for name, headers in (("../outside", {}), ("sparse", {"GNU.sparse.size": "0"})):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.pax_headers = headers
                with self.assertRaises(ValueError):
                    composer.compose(self.vendor, self.fixture([member]), self.sdk, self.output)
                self.assertFalse(self.output.exists())

    def test_corrupt_zstd_stream_is_not_published(self):
        self.fixture()
        self.vendor.write_bytes(self.vendor.read_bytes()[:-1])
        pin = hashlib.sha256(self.vendor.read_bytes()).hexdigest()
        with self.assertRaises((ValueError, tarfile.TarError)):
            composer.compose(self.vendor, pin, self.sdk, self.output)
        self.assertFalse(self.output.exists())

    def overlay(self, deletes=(), replaces=()):
        root = self.root / "overlay"
        root.mkdir(exist_ok=True)
        plan = self.root / "overlay-plan.json"
        plan.write_text(json.dumps({"schema_version": 1, "delete": list(deletes), "replace": list(replaces)}))
        return root, plan

    def test_overlay_replaces_default_target_and_deletes_enabled_link(self):
        default = "etc/systemd/system/default.target"
        enabled = "etc/systemd/system/multi-user.target.wants/vendor.service"
        members = []
        for name in (default, enabled):
            link = tarfile.TarInfo(name)
            link.type, link.linkname = tarfile.SYMTYPE, "/usr/lib/systemd/system/vendor.target"
            members.append(link)
        pin = self.fixture(members)
        overlay, plan = self.overlay([enabled], [default])
        target = overlay / default
        target.parent.mkdir(parents=True)
        target.symlink_to("/usr/lib/systemd/system/multi-user.target")
        settings = overlay / "etc/omam-apn.conf"
        settings.write_bytes(b"synthetic confidential setting")
        settings.chmod(0o600)
        result = composer.compose(self.vendor, pin, self.sdk, self.output, 321, overlay, plan)
        with self.read_output() as archive:
            entries = {composer.normalized_name(entry.name): entry for entry in archive}
            self.assertNotIn(enabled, entries)
            self.assertEqual(entries[default].linkname, "/usr/lib/systemd/system/multi-user.target")
            self.assertEqual((entries[default].uid, entries[default].gid, entries[default].mtime), (0, 0, 321))
            self.assertEqual((entries["etc"].uid, entries["etc"].mode), (41, 0o750))
            self.assertEqual(entries["etc/omam-apn.conf"].mode, 0o600)
        self.assertNotIn("synthetic confidential setting", json.dumps(result))

    def test_overlay_rejects_unplanned_overwrite_and_missing_replacement(self):
        pin = self.fixture()
        overlay, plan = self.overlay()
        (overlay / "etc").mkdir()
        replacement = overlay / "etc/vendor.conf"
        replacement.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "not in replace"):
            composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay, overlay_plan=plan)
        replacement.unlink()
        _, plan = self.overlay(replaces=["etc/vendor.conf"])
        with self.assertRaisesRegex(ValueError, "missing a planned replacement"):
            composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay, overlay_plan=plan)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".rootfs-compose-*")))

    def module_tree_fixture(self):
        members = []
        for name in ("usr", "usr/lib", "usr/lib/modules", "usr/lib/modules/old", "usr/lib/modules/old/kernel"):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            members.append(member)
        for name in ("usr/lib/modules/old/kernel/old.ko", "usr/lib/modules/old/modules.dep"):
            member = tarfile.TarInfo(name)
            member.size = 1
            members.append(member)
        return self.fixture(members), [member.name for member in members if member.name.startswith("usr/lib/modules/old")]

    def test_overlay_can_delete_a_complete_module_subtree(self):
        pin, deleted = self.module_tree_fixture()
        overlay, plan = self.overlay(deletes=deleted)

        composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay, overlay_plan=plan)

        with self.read_output() as archive:
            names = {composer.normalized_name(member.name) for member in archive}
            self.assertFalse(set(deleted) & names)
            self.assertIn("usr/lib/modules", names)
            self.assertIn("etc/vendor.conf", names)

    def test_overlay_cannot_delete_a_parent_while_retaining_a_child(self):
        pin, _ = self.module_tree_fixture()
        overlay, plan = self.overlay(deletes=["usr/lib/modules/old"])

        with self.assertRaisesRegex(ValueError, "leaves descendants"):
            composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay, overlay_plan=plan)

        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".rootfs-compose-*")))

    def test_overlay_cannot_write_below_a_deleted_parent(self):
        pin, deleted = self.module_tree_fixture()
        overlay, plan = self.overlay(deletes=deleted)
        new_file = overlay / "usr/lib/modules/old/new.ko"
        new_file.parent.mkdir(parents=True)
        new_file.write_bytes(b"forbidden replacement below deleted parent")

        with self.assertRaisesRegex(ValueError, "deleted path"):
            composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay, overlay_plan=plan)

        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".rootfs-compose-*")))

    def test_overlay_rejects_symlink_parent_and_unknown_plan_path(self):
        parent = tarfile.TarInfo("etc/redirect")
        parent.type, parent.linkname = tarfile.SYMTYPE, "/outside"
        pin = self.fixture([parent])
        overlay, plan = self.overlay()
        (overlay / "etc/redirect").mkdir(parents=True)
        (overlay / "etc/redirect/new.conf").write_bytes(b"new")
        with self.assertRaises(ValueError):
            composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay, overlay_plan=plan)
        _, plan = self.overlay(deletes=["does-not-exist"])
        with self.assertRaisesRegex(ValueError, "absent from the vendor"):
            composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay, overlay_plan=plan)
        self.assertFalse(self.output.exists())

    def test_overlay_plan_rejects_traversal_duplicates_and_conflicts(self):
        overlay, plan = self.overlay()
        for deletes, replaces in ((["../outside"], []), (["/outside"], []),
                                  (["etc/a", "./etc/a"], []), (["etc/a"], ["etc/a"])):
            with self.subTest(deletes=deletes, replaces=replaces):
                plan.write_text(json.dumps({"schema_version": 1, "delete": deletes, "replace": replaces}))
                with self.assertRaises(ValueError):
                    composer.read_overlay(overlay, plan)

    def test_overlay_cannot_implicitly_change_an_unplanned_vendor_hardlink(self):
        pin = self.fixture()  # etc/alias is a hardlink to etc/vendor.conf.
        overlay, plan = self.overlay(replaces=["etc/vendor.conf"])
        (overlay / "etc").mkdir()
        (overlay / "etc/vendor.conf").write_bytes(b"new content")
        with self.assertRaisesRegex(ValueError, "unplanned vendor hardlink"):
            composer.compose(self.vendor, pin, self.sdk, self.output, overlay_dir=overlay, overlay_plan=plan)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".rootfs-compose-*")))


if __name__ == "__main__":
    unittest.main()
