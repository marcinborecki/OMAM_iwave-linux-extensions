import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


DIRECTORY = Path(__file__).resolve().parents[2] / "tools/image"
SPEC = importlib.util.spec_from_file_location("inspect_rootfs", DIRECTORY / "inspect_rootfs.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RootfsInspectionTests(unittest.TestCase):
    def archive(self, directory, entries):
        content = io.BytesIO()
        with tarfile.open(fileobj=content, mode="w") as archive:
            for name, data, link in entries:
                item = tarfile.TarInfo(name)
                if link:
                    item.type = tarfile.LNKTYPE
                    item.linkname = link
                    archive.addfile(item)
                else:
                    item.size = len(data)
                    archive.addfile(item, io.BytesIO(data))
        path = Path(directory) / "rootfs.tar.zst"
        compressed = subprocess.run(["zstd", "-q", "-c"], input=content.getvalue(),
                                    capture_output=True, check=True, timeout=120).stdout
        path.write_bytes(compressed)
        return path

    def test_inventory_counts_files_once_without_extracting_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.archive(directory, [("./etc/os-release", b'ID=fsl-imx-xwayland\n', None),
                                             ("./usr/bin/mc", b"payload", None),
                                             ("./usr/bin/mc-alias", b"", "./usr/bin/mc")])
            result = MODULE.inspect_archive(path)
            self.assertEqual(result["regular_file_bytes"], 27)
            self.assertEqual(result["regular_file_4k_bytes"], 8192)
            self.assertEqual(result["member_counts"]["hardlink"], 1)
            self.assertEqual(result["tool_paths"], ["usr/bin/mc"])
            self.assertEqual(result["metadata"]["etc/os-release"], 'ID=fsl-imx-xwayland\n')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_bad_path_rejected_without_filesystem_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.archive(directory, [("../../escaped", b"bad", None)])
            with self.assertRaisesRegex(ValueError, "Unsafe archive member"):
                MODULE.inspect_archive(path)

    def test_corrupt_compression_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.archive(directory, [("usr/bin/mc", b"payload", None)])
            path.write_bytes(path.read_bytes()[:-4])
            with self.assertRaises((ValueError, tarfile.ReadError)):
                MODULE.inspect_archive(path)

    def test_vendor_layout_and_unknown_future_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "flash.uuu"
            script.write_text("PARTSTR=$'10M,500M,0c\\n600M,,83\\n';")
            self.assertEqual(MODULE.rootfs_offset(script), 629145600)
            script.write_text("sfdisk /dev/mmcblk0 < new-layout")
            with self.assertRaisesRegex(ValueError, "Unrecognised"):
                MODULE.rootfs_offset(script)

    def test_fit_includes_filesystem_extra_and_working_budgets(self):
        small = MODULE.capacity(8000, 600, 5500, 200, 1000, 10)
        large = MODULE.capacity(16000, 600, 5500, 200, 1000, 10)
        self.assertFalse(small["fits_estimate"])
        self.assertEqual(small["remaining_after_budgets_bytes"], -40)
        self.assertTrue(large["fits_estimate"])
        self.assertFalse(large["device_verified"])


if __name__ == "__main__":
    unittest.main()
