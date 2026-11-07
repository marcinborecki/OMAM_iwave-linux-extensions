"""Offline boundary tests; fixtures are deliberately tiny, synthetic SDK archives."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


DIRECTORY = Path(__file__).resolve().parents[2] / "tools/image"
SPEC = importlib.util.spec_from_file_location("sdk_payload", DIRECTORY / "sdk_payload.py")
payload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(payload)


class SdkPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.output = self.root / "payload"

    def archive(self, version="10.0.100", extra=None):
        archive = self.cache / f"dotnet-sdk-{version}-linux-arm64.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            root = tarfile.TarInfo("./")
            root.type = tarfile.DIRTYPE
            bundle.addfile(root)
            for name, content in (("./dotnet", version.encode()),
                                  (f"./sdk/{version}/SDK.txt", b"SDK"),
                                  (f"./shared/Microsoft.NETCore.App/{version}/runtime.txt", b"runtime")):
                entry = tarfile.TarInfo(name)
                entry.size = len(content)
                entry.mode = 0o755
                bundle.addfile(entry, io.BytesIO(content))
            if extra:
                bundle.addfile(extra, io.BytesIO(b"x" * extra.size))
        return {"version": version, "url": "https://builds.dotnet.microsoft.com/fixture.tar.gz",
                "metadata_url": f"{payload.METADATA_BASE}/10.0/releases.json",
                "sha512": hashlib.sha512(archive.read_bytes()).hexdigest()}

    def manifest(self, *pins):
        return {"schema_version": 1, "rid": "linux-arm64", "sdks": list(pins)}

    def test_two_sdks_coexist_and_newest_host_wins(self):
        old, new = self.archive(), self.archive("11.0.100-rc.1")
        report = payload.stage(self.manifest(old, new), self.cache, self.output)
        self.assertEqual(self.output.joinpath("dotnet").read_text(), new["version"])
        for pin in (old, new):
            self.assertTrue((self.output / "sdk" / pin["version"] / "SDK.txt").is_file())
        self.assertEqual(report["host_from_sdk"], new["version"])
        self.assertEqual(report["tree_sha256"], payload.tree_sha256(self.output))
        self.assertGreater(report["payload"]["logical_bytes"], 0)
        self.assertEqual(report, json.loads((self.output / "sdk-payload-report.json").read_text()))

    def test_second_checksum_mismatch_prevents_any_extraction(self):
        old, new = self.archive(), self.archive("11.0.100-rc.1")
        new["sha512"] = "0" * 128
        with patch.object(payload, "extract_archive") as extract:
            with self.assertRaisesRegex(ValueError, "SHA512 mismatch"):
                payload.stage(self.manifest(old, new), self.cache, self.output)
            extract.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".sdk-stage-*")))

    def test_existing_destination_is_immutable_even_when_empty(self):
        self.output.mkdir()
        with patch.object(payload, "cached_archive") as download:
            with self.assertRaises(FileExistsError):
                payload.stage(self.manifest(self.archive()), self.cache, self.output)
            download.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_rejects_traversal_absolute_paths_and_special_files(self):
        for name, kind in (("../outside", tarfile.REGTYPE), ("/tmp/outside", tarfile.REGTYPE),
                           ("fifo", tarfile.FIFOTYPE), ("device", tarfile.CHRTYPE)):
            with self.subTest(name=name):
                entry = tarfile.TarInfo(name)
                entry.type = kind
                pin = self.archive(extra=entry)
                with self.assertRaises(ValueError):
                    payload.stage(self.manifest(pin), self.cache, self.output)
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.root.glob(".sdk-stage-*")))
        self.assertFalse((self.root / "outside").exists())

    def test_rejects_symlink_and_hardlink_escapes(self):
        for kind, target in ((tarfile.SYMTYPE, "../../outside"),
                             (tarfile.LNKTYPE, "../outside"), (tarfile.SYMTYPE, "/tmp/outside")):
            with self.subTest(kind=kind, target=target):
                entry = tarfile.TarInfo("nested/link")
                entry.type, entry.linkname = kind, target
                pin = self.archive(extra=entry)
                with self.assertRaises(ValueError):
                    payload.stage(self.manifest(pin), self.cache, self.output)
                self.assertFalse(self.output.exists())

    def test_download_mismatch_removes_temporary_cache_file(self):
        pin = {"version": "10.0.100", "url": "https://builds.dotnet.microsoft.com/fixture.tar.gz",
               "metadata_url": f"{payload.METADATA_BASE}/10.0/releases.json", "sha512": "0" * 128}
        response = io.BytesIO(b"corrupted download")
        response.geturl = lambda: pin["url"]
        with patch.object(payload, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "SHA512 mismatch"):
                payload.stage(self.manifest(pin), self.cache, self.output)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertFalse(self.output.exists())

    def test_manifest_rejects_reverse_sdk_order(self):
        with self.assertRaisesRegex(ValueError, "oldest to newest"):
            payload.validate_manifest(self.manifest(self.archive("11.0.100-rc.1"), self.archive()))

    def test_resolver_pins_latest_metadata_sdk_for_correct_rid(self):
        metadata = {"channel-version": "10.0", "latest-sdk": "10.0.400", "releases": [
            {"sdks": [{"version": "10.0.400", "files": [
                {"rid": "linux-x64", "name": "dotnet-sdk-linux-x64.tar.gz", "url": "ignored"},
                {"rid": "linux-arm64", "name": "dotnet-sdk-linux-arm64.tar.gz",
                 "url": "https://builds.dotnet.microsoft.com/sdk.tar.gz", "hash": "a" * 128}]}]}]}
        with patch.object(payload, "urlopen", return_value=io.BytesIO(json.dumps(metadata).encode())):
            pin = payload.resolve("10.0")
        self.assertEqual(pin["version"], "10.0.400")
        self.assertEqual(pin["sha512"], "a" * 128)
        self.assertEqual(pin["metadata_url"], f"{payload.METADATA_BASE}/10.0/releases.json")


if __name__ == "__main__":
    unittest.main()
