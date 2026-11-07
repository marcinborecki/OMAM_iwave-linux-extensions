#!/usr/bin/env python3
"""Read ELF64/AArch64 module contracts without executing or loading a module."""
from __future__ import annotations

from pathlib import Path
import re
import struct


class ElfModule:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        if len(self.data) < 64 or self.data[:6] != b"\x7fELF\x02\x01":
            raise ValueError(f"Not ELF64 little-endian: {path.name}")
        values = struct.unpack_from("<16sHHIQQQIHHHHHH", self.data)
        if values[1:3] != (1, 183) or values[3] != 1 or self.data[6] != 1:
            raise ValueError(f"Not an AArch64 relocatable module: {path.name}")
        offset, entry_size, count, names = values[6], values[11], values[12], values[13]
        if entry_size != 64 or not count or names >= count:
            raise ValueError("Unsupported ELF section table")
        self.sections = []
        for i in range(count):
            self.sections.append(struct.unpack("<IIQQQQIIQQ", self.part(offset + i * 64, 64)))
        strings = self.section_data(names)
        self.names = {self.string(strings, section[0]): i for i, section in enumerate(self.sections)}
        self.symbols = []
        for section in self.sections:
            if section[1] != 2:  # SHT_SYMTAB
                continue
            if section[9] != 24 or section[5] % 24 or section[6] >= count:
                raise ValueError("Malformed symbol table")
            strings = self.section_data(section[6])
            for i in range(0, section[5], 24):
                sym = struct.unpack("<IBBHQQ", self.part(section[4] + i, 24))
                self.symbols.append((self.string(strings, sym[0]), *sym[1:]))

    def part(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ValueError("ELF range outside file")
        return self.data[offset:offset + size]

    @staticmethod
    def string(data: bytes, offset: int) -> str:
        if offset >= len(data):
            raise ValueError("String offset outside ELF string table")
        end = data.find(b"\0", offset)
        if end < 0:
            raise ValueError("Unterminated ELF string")
        return data[offset:end].decode("utf-8")

    def section_data(self, index: int) -> bytes:
        if index >= len(self.sections):
            raise ValueError("Invalid ELF section index")
        section = self.sections[index]
        if section[1] == 8:  # NOBITS has no on-disk data.
            raise ValueError("Cannot read an ELF NOBITS section")
        return self.part(section[4], section[5])

    def named(self, name: str) -> bytes:
        if name not in self.names:
            raise ValueError(f"Missing module section {name}: {self.path.name}")
        return self.section_data(self.names[name])

    def metadata(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for entry in self.named(".modinfo").split(b"\0"):
            if b"=" in entry:
                key, value = entry.decode().split("=", 1)
                result.setdefault(key, []).append(value)
        return result

    def versions(self) -> dict[str, int]:
        data = self.named("__versions")
        if not data or len(data) % 64:
            raise ValueError("Missing or malformed MODVERSIONS entries")
        result = {}
        for offset in range(0, len(data), 64):
            crc, name = struct.unpack_from("<Q56s", data, offset)
            if b"\0" not in name or crc > 0xffffffff:
                raise ValueError("Malformed MODVERSIONS symbol")
            decoded = name.split(b"\0", 1)[0].decode()
            if decoded in result:
                raise ValueError("Duplicate MODVERSIONS symbol")
            result[decoded] = crc
        if "module_layout" not in result:
            raise ValueError("Module lacks the loader's module_layout CRC")
        return result

    def exports(self) -> dict[str, int]:
        result = {}
        for name, info, other, section, value, size in self.symbols:
            if not name.startswith("__crc_"):
                continue
            if section == 0xfff1:  # SHN_ABS
                crc = value
            else:
                data = self.section_data(section)
                if value + 4 > len(data):
                    raise ValueError("Export CRC outside its section")
                crc = struct.unpack_from("<I", data, value)[0]
            if crc > 0xffffffff:
                raise ValueError("Invalid export CRC")
            result[name[6:]] = crc
        return result

    def export_contracts(self) -> tuple[dict[str, str], set[str]]:
        namespaces, gpl = {}, set()
        section_names = {index: name for name, index in self.names.items()}
        for name, info, other, section, value, size in self.symbols:
            if name.startswith("__kstrtabns_"):
                namespaces[name[len("__kstrtabns_"):]] = self.string(self.section_data(section), value)
            if name.startswith("__ksymtab_") and section_names.get(section) == "__ksymtab_gpl":
                gpl.add(name[len("__ksymtab_"):])
        return namespaces, gpl

    def relocations(self) -> set[int]:
        result = set()
        for section in self.sections:
            if section[1] == 9 and section[5]:
                raise ValueError("AArch64 REL relocation is unsupported")
            if section[1] != 4:  # SHT_RELA
                continue
            if section[9] != 24 or section[5] % 24:
                raise ValueError("Malformed RELA table")
            for i in range(0, section[5], 24):
                offset, info, addend = struct.unpack("<QQq", self.part(section[4] + i, 24))
                result.add(info & 0xffffffff)
        return result

    def layout_size(self) -> int:
        return self.sections[self.names[".gnu.linkonce.this_module"]][5]


def supported_relocations(source: Path) -> dict[int, str]:
    definitions = (source / "arch/arm64/include/asm/elf.h").read_text()
    constants = {name: int(value, 0) for name, value in
                 re.findall(r"^#define\s+(R_(?:ARM|AARCH64)_\w+)\s+(0x[0-9a-fA-F]+|[0-9]+)\b", definitions, re.M)}
    cases = set(re.findall(r"case (R_(?:ARM|AARCH64)_\w+):", (source / "arch/arm64/kernel/module.c").read_text()))
    if not cases or any(case not in constants for case in cases):
        raise ValueError("Cannot derive all supported relocations from the exact loader")
    return {constants[case]: case for case in cases}


def check_versions(imports: dict[str, int], exports: dict[str, int]) -> None:
    mismatches = {name: {"module": f"0x{crc:08x}", "provider":
                        None if name not in exports else f"0x{exports[name]:08x}"}
                  for name, crc in imports.items() if exports.get(name) != crc}
    if mismatches:
        raise ValueError("Versioned imports do not match providers: " + repr(mismatches))


def check_vermagic(observed: str, expected: str, *, preserved_stock: bool, has_crcs: bool) -> None:
    if preserved_stock:
        # Exact kernel/module/version.c same_magic() contract; no binary rewriting.
        if not has_crcs or observed.partition(" ")[2] != expected.partition(" ")[2]:
            raise ValueError("Stock module cannot use the CRC-qualified loader vermagic rule")
    elif observed != expected:
        raise ValueError("Newly compiled module vermagic does not match the candidate")


def check_namespaces(imports: dict[str, int], namespaces: dict[str, str], declared: list[str]) -> None:
    required = {namespaces.get(name, "") for name in imports} - {""}
    missing = required - set(declared)
    if missing:
        raise ValueError("Missing module import namespace: " + repr(sorted(missing)))


def gpl_compatible_licenses(source: Path) -> set[str]:
    text = (source / "include/linux/license.h").read_text()
    values = set(re.findall(r'strcmp\(license, "([^"]+)"\)\s*==\s*0', text))
    if not values:
        raise ValueError("Cannot derive GPL-compatible module licenses from the loader source")
    return values


def check_gpl_imports(imports: dict[str, int], gpl_only: set[str], licenses: list[str], allowed: set[str]) -> None:
    # kernel/module/main.c obtains the first license with get_modinfo().
    if imports.keys() & gpl_only and (not licenses or licenses[0] not in allowed):
        raise ValueError("GPL-only import is not permitted by the module's first license")
