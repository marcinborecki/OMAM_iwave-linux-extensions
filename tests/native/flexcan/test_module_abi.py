"""Independent ELF fixtures and loader-contract tests; no kernel or device operation."""
from pathlib import Path
import importlib.util
import os
import signal
import struct
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/kernel"))
from module_abi import (ElfModule, check_versions, check_vermagic, supported_relocations,
                        check_namespaces, gpl_compatible_licenses, check_gpl_imports)
from process_group import stop


def elf_fixture(*, architecture=183, versions=None, relocation_type=4):
    names = ["", ".shstrtab", ".modinfo", "__versions", ".gnu.linkonce.this_module", ".strtab", ".symtab", "__kcrctab", ".rela.test"]
    strings = b"\0" + b"\0".join(n.encode() for n in names[1:]) + b"\0"
    offsets = {n: strings.find(n.encode() + b"\0") for n in names}
    if versions is None:
        versions = [(0xaabbccdd, "module_layout"), (0x12345678, "example")]
    payloads = [b"", strings, b"vermagic=6.12.34-new SMP preempt modversions aarch64\0depends=provider\0",
                b"".join(struct.pack("<Q56s", value, name.encode()) for value, name in versions),
                bytes(1088), b"\0__crc_exported\0",
                bytes(24) + struct.pack("<IBBHQQ", 1, 0, 0, 7, 0, 0),
                struct.pack("<I", 0x87654321), struct.pack("<QQq", 0, 283, 0)]
    body = bytearray(64)
    sections = []
    for i, data in enumerate(payloads):
        kind = [0, 3, 1, 1, 1, 3, 2, 1, relocation_type][i]
        sections.append((offsets[names[i]], kind, 0, 0, len(body), len(data), 5 if i == 6 else 0,
                         0, 8, 24 if i in (6, 8) else 0))
        body.extend(data)
    table = len(body)
    body.extend(b"".join(struct.pack("<IIQQQQIIQQ", *section) for section in sections))
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    body[:64] = struct.pack("<16sHHIQQQIHHHHHH", ident, 1, architecture, 1, 0, 0, table, 0, 64, 0, 0, 64, len(sections), 1)
    return bytes(body)


class ModuleContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="omam-module-abi-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def module(self, data):
        path = self.root / "fixture.ko"
        path.write_bytes(data)
        return ElfModule(path)

    def test_literal_elf_contract(self):
        module = self.module(elf_fixture())
        self.assertEqual(module.versions(), {"module_layout": 0xaabbccdd, "example": 0x12345678})
        self.assertEqual(module.exports(), {"exported": 0x87654321})
        self.assertEqual(module.layout_size(), 1088)
        self.assertEqual(module.relocations(), {283})
        self.assertEqual(module.metadata()["depends"], ["provider"])

    def test_rejects_wrong_machine_and_truncated_input(self):
        with self.assertRaisesRegex(ValueError, "AArch64"):
            self.module(elf_fixture(architecture=62))
        with self.assertRaisesRegex(ValueError, "range outside"):
            self.module(elf_fixture()[:-1])
        with self.assertRaisesRegex(ValueError, "ELF64"):
            self.module(b"not an ELF file")

    def test_versions_require_layout_and_unique_names(self):
        with self.assertRaisesRegex(ValueError, "module_layout"):
            self.module(elf_fixture(versions=[(12, "example")])).versions()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.module(elf_fixture(versions=[(12, "module_layout"), (13, "module_layout")])).versions()
        with self.assertRaisesRegex(ValueError, "Malformed"):
            self.module(elf_fixture(versions=[(0x100000000, "module_layout")])).versions()

    def test_provider_crc_must_exist_and_match(self):
        check_versions({"module_layout": 12, "needed": 45}, {"module_layout": 12, "needed": 45, "extra": 90})
        with self.assertRaisesRegex(ValueError, "needed"):
            check_versions({"needed": 45}, {"needed": 46})
        with self.assertRaisesRegex(ValueError, "needed"):
            check_versions({"needed": 45}, {})

    def test_original_release_is_only_ignored_for_versioned_stock_modules(self):
        old = "6.12.34-vendor SMP preempt mod_unload modversions aarch64"
        new = "6.12.34-omam SMP preempt mod_unload modversions aarch64"
        check_vermagic(old, new, preserved_stock=True, has_crcs=True)
        with self.assertRaises(ValueError):
            check_vermagic(old, new, preserved_stock=True, has_crcs=False)
        with self.assertRaises(ValueError):
            check_vermagic(old, new.replace("preempt ", ""), preserved_stock=True, has_crcs=True)
        with self.assertRaises(ValueError):
            check_vermagic(old, new, preserved_stock=False, has_crcs=True)
        check_vermagic(new, new, preserved_stock=False, has_crcs=True)

    def test_loader_relocation_set_is_derived_from_both_source_files(self):
        header = self.root / "arch/arm64/include/asm/elf.h"
        source = self.root / "arch/arm64/kernel/module.c"
        header.parent.mkdir(parents=True)
        source.parent.mkdir(parents=True)
        header.write_text("#define R_ARM_NONE 0\n#define R_AARCH64_NONE 256\n#define R_AARCH64_CALL26 283\n")
        source.write_text("case R_ARM_NONE:\ncase R_AARCH64_NONE:\ncase R_AARCH64_CALL26:\n")
        self.assertEqual(supported_relocations(self.root), {0: "R_ARM_NONE", 256: "R_AARCH64_NONE", 283: "R_AARCH64_CALL26"})
        source.write_text(source.read_text() + "case R_AARCH64_UNKNOWN:\n")
        with self.assertRaisesRegex(ValueError, "Cannot derive"):
            supported_relocations(self.root)

    def test_import_namespace_is_required_by_export_contract(self):
        check_namespaces({"probe": 9}, {"probe": "IIO_LSM6DSX"}, ["IIO_LSM6DSX"])
        with self.assertRaisesRegex(ValueError, "IIO_LSM6DSX"):
            check_namespaces({"probe": 9}, {"probe": "IIO_LSM6DSX"}, [])
        check_namespaces({"probe": 9}, {"probe": ""}, [])

    def test_license_allowance_comes_from_exact_loader_source(self):
        path = self.root / "include/linux/license.h"
        path.parent.mkdir(parents=True)
        path.write_text('return strcmp(license, "GPL") == 0 || strcmp(license, "Dual BSD/GPL") == 0;')
        self.assertEqual(gpl_compatible_licenses(self.root), {"GPL", "Dual BSD/GPL"})
        path.write_text("unknown loader")
        with self.assertRaisesRegex(ValueError, "Cannot derive"):
            gpl_compatible_licenses(self.root)

    def test_only_first_module_license_controls_gpl_exports(self):
        check_gpl_imports({"probe": 1}, {"probe"}, ["GPL"], {"GPL"})
        check_gpl_imports({"ordinary": 1}, {"probe"}, [], {"GPL"})
        with self.assertRaisesRegex(ValueError, "first license"):
            check_gpl_imports({"probe": 1}, {"probe"}, ["Proprietary", "GPL"], {"GPL"})
        with self.assertRaisesRegex(ValueError, "first license"):
            check_gpl_imports({"probe": 1}, {"probe"}, [], {"GPL"})

    def test_rejects_unimplemented_rel_section(self):
        with self.assertRaisesRegex(ValueError, "REL relocation"):
            self.module(elf_fixture(relocation_type=9)).relocations()

    def test_supervisor_stops_descendant_after_leader_exit(self):
        code = ("import subprocess,sys; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                "print(p.pid,flush=True)")
        parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                  text=True, start_new_session=True)
        try:
            output, _ = parent.communicate(timeout=5)
            self.assertEqual(parent.returncode, 0)
            child_pid = int(output.strip())
            stop(parent)
            status = Path(f"/proc/{child_pid}/stat")
            if status.exists():
                self.assertEqual(status.read_text().rsplit(")", 1)[1].split()[0], "Z",
                                 "supervisor left a running descendant after the leader exited")
        finally:
            # Cleanup is independent of the subject, including on the RED path.
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            parent.wait(timeout=5)

    def test_supervisor_stops_its_own_process(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            stop(process)
            self.assertIsNotNone(process.poll())
        finally:
            stop(process)


if __name__ == "__main__":
    unittest.main(verbosity=2)
