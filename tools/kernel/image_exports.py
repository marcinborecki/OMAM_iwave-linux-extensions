# SPDX-License-Identifier: GPL-2.0-only
"""Read pinned-layout ARM64 Linux 6.12 PREL32 exports from Image bytes.

This is a layout-specific parser, not a general ELF or arbitrary Image parser.
Callers must independently compare it with the candidate Module.symvers.
"""
import re
import struct


class ImageExportError(ValueError):
    """Unsupported or malformed export-table layout."""


def symbol_table(image: bytes) -> list[tuple[int, str, bool, str]]:
    """(crc, name, gpl, namespace) of every export, from the arm64 PREL32 __ksymtab/__ksymtab_gpl and __kcrctab tables.

    6.12 linker-script order: __ksymtab, __ksymtab_gpl, __kcrctab, __kcrctab_gpl, __ksymtab_strings; both symbol tables
    are sorted by name, so the CRC array is index-aligned with them and ends where __ksymtab_strings begins.
    """
    if image[0x38:0x3C] != b"ARM\x64":
        raise ImageExportError("not an arm64 Image")
    pattern = re.compile(rb"(?:[A-Za-z_][A-Za-z0-9_.$]{0,200}\x00(?:[A-Za-z0-9_.]{0,64})\x00){1000,}")
    matches = list(pattern.finditer(image))
    if not matches:
        raise ImageExportError("no supported export string table in the Image")
    best = max(matches, key=lambda match: match.end() - match.start())
    low, high = max(0, best.start() - 65536), min(len(image), best.end() + 262144)
    identifier, namespace = re.compile(rb"[A-Za-z_][A-Za-z0-9_.$]*"), re.compile(rb"[A-Za-z0-9_.]*")

    def entry(offset):
        if offset + 12 > len(image):
            return None
        _, name_rel, ns_rel = struct.unpack_from("<iii", image, offset)
        name_at, ns_at = offset + 4 + name_rel, offset + 8 + ns_rel
        if not (low <= name_at < high and low <= ns_at < high):
            return None
        name_end = image.find(b"\x00", name_at)
        if name_end < 0:
            return None
        name = image[name_at:name_end]
        ns_end = image.find(b"\x00", ns_at)
        if not identifier.fullmatch(name) or ns_end < 0 or not namespace.fullmatch(image[ns_at:ns_end]):
            return None
        return name.decode(), image[ns_at:ns_end].decode()

    first = max(0, best.start() - 4 * 1024 * 1024)
    entries = {}
    for offset in range(first - first % 4, best.start(), 4):
        value = entry(offset)
        if value:
            entries[offset] = value
    runs: list[list[int]] = []
    for offset in sorted(entries):
        if offset - 12 not in entries:
            end = offset + 12
            while end in entries:
                end += 12
            runs.append([offset, end])
    if not runs:
        raise ImageExportError("no supported PREL32 export table in the Image")
    start, end = max(runs, key=lambda item: item[1] - item[0])
    table = [entries[offset] for offset in range(start, end, 12)]
    splits = [i for i in range(1, len(table)) if table[i][0] < table[i - 1][0]]
    if len(splits) != 1:
        raise ImageExportError("unexpected normal/GPL export table boundary")
    split = splits[0]
    normal, gpl = table[:split], table[split:]
    if any(part[i][0] >= part[i + 1][0]
           for part in (normal, gpl) for i in range(len(part) - 1)):
        raise ImageExportError("unexpected export table layout in the Image")
    if end + 4 * len(table) >= len(image):
        raise ImageExportError("truncated CRC table in the Image")
    crcs = struct.unpack_from(f"<{len(table)}I", image, end)
    strings_start = end + 4 * len(table)
    if not image[strings_start:strings_start + 1].isalpha() and image[strings_start:strings_start + 1] != b"_":
        raise ImageExportError("the CRC table does not end at __ksymtab_strings")
    return [(crcs[i], name, i >= len(normal), ns) for i, (name, ns) in enumerate(normal + gpl)]
