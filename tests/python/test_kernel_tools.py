# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic Image/manifest/resource boundaries, independent of private inputs."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/kernel"))
from image_exports import ImageExportError, symbol_table
from qualify import verify_imu_package
from build import constraints


def image_fixture():
    """Literal linker layout: two sorted PREL32 tables, aligned CRCs, then strings."""
    exports = [(f"normal_{i:04d}", "", False, 0x12340000 + i) for i in range(600)]
    exports += [(f"gpl_{i:04d}", "EXAMPLE", True, 0x56780000 + i) for i in range(600)]
    table_at = 4096
    strings_at = table_at + len(exports) * 16
    data = bytearray(strings_at)
    data[0x38:0x3c] = b"ARM\x64"
    for i, (name, ns, gpl, crc) in enumerate(exports):
        at = table_at + i * 12
        name_at = len(data)
        data.extend(name.encode() + b"\0")
        ns_at = len(data)
        data.extend(ns.encode() + b"\0")
        struct.pack_into("<iii", data, at, 0, name_at - at - 4, ns_at - at - 8)
        struct.pack_into("<I", data, table_at + len(exports) * 12 + i * 4, crc)
    return bytes(data), [(crc, name, gpl, ns) for name, ns, gpl, crc in exports]


class ImageExports(unittest.TestCase):
    def test_literal_linker_layout_preserves_crc_license_and_namespace(self):
        image, expected = image_fixture()
        self.assertEqual(symbol_table(image), expected)

    def test_wrong_architecture_is_refused(self):
        image, _ = image_fixture()
        with self.assertRaisesRegex(ImageExportError, "arm64"):
            symbol_table(bytes(64) + image[64:])

    def test_no_export_table_is_refused(self):
        image = bytearray(128)
        image[0x38:0x3c] = b"ARM\x64"
        with self.assertRaisesRegex(ImageExportError, "export string"):
            symbol_table(bytes(image))

    def test_single_sorted_table_cannot_masquerade_as_two_tables(self):
        image, _ = image_fixture()
        # Remove the GPL-order reset while keeping realistic strings and relocations.
        image = image.replace(b"gpl_", b"zpl_")
        with self.assertRaisesRegex(ImageExportError, "boundary"):
            symbol_table(image)


class ImuPackage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="iwave-imu-package-test-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.release = "6.12.34-iwave-rel33-policy1"
        self.manifest = {"schema": "omam.imu-modules.v1", "kernelRelease": self.release, "modules": []}
        for name in ("st_lsm6dsx.ko", "st_lsm6dsx_i2c.ko"):
            data = ("fixture:" + name).encode()
            (self.directory / name).write_bytes(data)
            self.manifest["modules"].append({"file": name, "sha256": hashlib.sha256(data).hexdigest()})
        self.write_manifest()

    def write_manifest(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))

    def test_complete_exact_package_is_accepted(self):
        verify_imu_package(self.directory, self.release)

    def test_changed_module_is_refused(self):
        (self.directory / "st_lsm6dsx.ko").write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "differs"):
            verify_imu_package(self.directory, self.release)

    def test_wrong_release_is_refused(self):
        with self.assertRaisesRegex(ValueError, "contract"):
            verify_imu_package(self.directory, "6.12.34-other")

    def test_additional_payload_is_refused(self):
        (self.directory / "extra.ko").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "contract"):
            verify_imu_package(self.directory, self.release)

    def test_duplicate_module_names_are_refused(self):
        self.manifest["modules"][1] = self.manifest["modules"][0]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "contract"):
            verify_imu_package(self.directory, self.release)

    def test_external_symlink_is_refused_even_with_matching_bytes(self):
        module = self.directory / "st_lsm6dsx.ko"
        stored = self.directory / "stored"
        module.rename(stored)
        module.symlink_to(stored)
        stored.rename(self.directory.parent / (self.directory.name + "-module"))
        external = self.directory.parent / (self.directory.name + "-module")
        self.addCleanup(external.unlink)
        module.unlink()
        module.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "differs"):
            verify_imu_package(self.directory, self.release)


class ResourceBounds(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(allowed_cpus="1,2", cpu_percent=150, memory_gib=6, jobs=2, nice_min=10)
        self.files = {"/proc/self/cgroup": "0::/fixture\n", "cpu.max": "150000 100000",
                      "memory.max": str(6 * 1024**3), "memory.swap.max": "0"}
        def read(path, *args, **kwargs):
            return self.files.get(str(path), self.files.get(path.name))
        for mocked in (patch.object(Path, "read_text", read),
                       patch("build.os.sched_getaffinity", return_value={1, 2}),
                       patch("build.os.getpriority", return_value=10)):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_explicit_host_cpu_numbers_replace_fixed_private_slot(self):
        _, actual = constraints(self.args)
        self.assertEqual(actual["affinity"], [1, 2])
        self.assertEqual(actual["jobs"], 2)

    def test_unbounded_cpu_is_refused(self):
        self.files["cpu.max"] = "max 100000"
        with self.assertRaisesRegex(ValueError, "CPU"):
            constraints(self.args)

    def test_excessive_cpu_is_refused(self):
        self.files["cpu.max"] = "200000 100000"
        with self.assertRaisesRegex(ValueError, "CPU"):
            constraints(self.args)

    def test_swap_is_refused(self):
        self.files["memory.swap.max"] = "1024"
        with self.assertRaisesRegex(ValueError, "no-swap"):
            constraints(self.args)

    def test_excessive_memory_is_refused(self):
        self.files["memory.max"] = str(7 * 1024**3)
        with self.assertRaisesRegex(ValueError, "Memory"):
            constraints(self.args)

    def test_jobs_must_fit_reserved_cpus(self):
        self.args.jobs = 3
        with self.assertRaisesRegex(ValueError, "resources"):
            constraints(self.args)


if __name__ == "__main__":
    unittest.main()
