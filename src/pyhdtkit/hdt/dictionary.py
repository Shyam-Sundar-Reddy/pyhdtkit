"""FourSectionDictionary reader: the four PlainFrontCoding (PFC) sections
(shared, subjects, predicates, objects) that make up an HDT dictionary. See
DECISIONS.md sections 4, 5, and 7.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyhdtkit.hdt.binio import crc8, crc32c, decode_log_array, vbyte_decode
from pyhdtkit.hdt.control_info import parse_control_info


def decode_pfc_section(data: bytes, pos: int) -> tuple[list[str], int]:
    """Decode one DictionarySectionPlainFrontCoding. Returns (strings in
    ID order — ID = 1-based rank, i.e. ``strings[0]`` is ID 1 — and the
    position right after this section).
    """
    sec_start = pos
    _type = data[pos]
    pos += 1
    numstrings, pos = vbyte_decode(data, pos)
    nbytes, pos = vbyte_decode(data, pos)
    blocksize, pos = vbyte_decode(data, pos)
    header_end = pos
    stored_crc8 = data[pos]
    pos += 1
    if crc8(data[sec_start:header_end]) != stored_crc8:
        raise ValueError(f"PFC section header CRC8 mismatch at offset {sec_start}")

    block_offsets, pos = decode_log_array(data, pos)

    buf = data[pos : pos + nbytes]
    pos += nbytes
    stored_crc32 = int.from_bytes(data[pos : pos + 4], "little")
    pos += 4
    if crc32c(buf) != stored_crc32:
        raise ValueError(f"PFC string buffer CRC32C mismatch at offset {sec_start}")

    if numstrings == 0:
        return [], pos

    strings: list[str] = []
    num_blocks = len(block_offsets) - 1  # last entry is the end-of-buffer sentinel
    for block in range(num_blocks):
        p = block_offsets[block]
        block_count = min(blocksize, numstrings - block * blocksize)
        prev = ""
        for i in range(block_count):
            if i == 0:
                nul = buf.index(b"\x00", p)
                s = buf[p:nul].decode("utf-8")
                p = nul + 1
            else:
                shared, p = vbyte_decode(buf, p)
                nul = buf.index(b"\x00", p)
                suffix = buf[p:nul].decode("utf-8")
                s = prev[:shared] + suffix
                p = nul + 1
            strings.append(s)
            prev = s

    return strings, pos


@dataclass(frozen=True)
class FourSectionDictionary:
    shared: list[str]
    subjects: list[str]
    predicates: list[str]
    objects: list[str]

    def subject_string(self, id_: int) -> str:
        return self.shared[id_ - 1] if id_ <= len(self.shared) else self.subjects[id_ - 1 - len(self.shared)]

    def object_string(self, id_: int) -> str:
        return self.shared[id_ - 1] if id_ <= len(self.shared) else self.objects[id_ - 1 - len(self.shared)]

    def predicate_string(self, id_: int) -> str:
        return self.predicates[id_ - 1]


def decode_four_section_dictionary(data: bytes, pos: int) -> tuple[FourSectionDictionary, int]:
    """Decode a Dictionary Control Info block followed by its four PFC
    sections (shared, subjects, predicates, objects — fixed order).
    """
    info, pos = parse_control_info(data, pos)
    if "dictionaryFour" not in info.format:
        raise ValueError(f"unsupported dictionary format: {info.format!r} (only FourSectionDictionary is implemented)")

    shared, pos = decode_pfc_section(data, pos)
    subjects, pos = decode_pfc_section(data, pos)
    predicates, pos = decode_pfc_section(data, pos)
    objects, pos = decode_pfc_section(data, pos)

    return FourSectionDictionary(shared, subjects, predicates, objects), pos
