"""FourSectionDictionary reader: the four PlainFrontCoding (PFC) sections
(shared, subjects, predicates, objects) that make up an HDT dictionary. See
DECISIONS.md sections 4, 5, and 7.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyhdtkit.hdt.binio import crc8, crc32c, decode_log_array, encode_log_array, vbyte_decode, vbyte_encode
from pyhdtkit.hdt.control_info import build_control_info, parse_control_info

# hdt-cpp's own default (confirmed observed in the snikmeta.hdt fixture's
# PFC sections) — strings per front-coding block.
DEFAULT_BLOCKSIZE = 16


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
        prev_bytes = b""
        for i in range(block_count):
            if i == 0:
                nul = buf.index(b"\x00", p)
                raw = buf[p:nul]
                p = nul + 1
            else:
                # Shared-prefix length is a byte count (front-coding operates
                # on the raw UTF-8 bytes), not a character count — slicing
                # the decoded str here would corrupt any string containing
                # multi-byte characters.
                shared, p = vbyte_decode(buf, p)
                nul = buf.index(b"\x00", p)
                raw = prev_bytes[:shared] + buf[p:nul]
                p = nul + 1
            strings.append(raw.decode("utf-8"))
            prev_bytes = raw

    return strings, pos


def _common_prefix_len(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def encode_pfc_section(strings: list[str], blocksize: int = DEFAULT_BLOCKSIZE) -> tuple[bytes, int]:
    """Inverse of ``decode_pfc_section``. ``strings`` must already be
    sorted (byte-lexicographic) — that sort order IS the ID assignment
    (ID = 1-based rank), the same convention ``decode_pfc_section`` reads
    back. Returns (encoded section bytes, string-buffer byte length).
    """
    buf = bytearray()
    block_offsets: list[int] = []
    prev = b""
    for i, s in enumerate(strings):
        raw = s.encode("utf-8")
        if i % blocksize == 0:
            block_offsets.append(len(buf))
            buf += raw + b"\x00"
        else:
            shared = _common_prefix_len(prev, raw)
            buf += vbyte_encode(shared) + raw[shared:] + b"\x00"
        prev = raw
    block_offsets.append(len(buf))  # end-of-buffer sentinel, mirrors decode_pfc_section

    header = bytes([2]) + vbyte_encode(len(strings)) + vbyte_encode(len(buf)) + vbyte_encode(blocksize)
    header = header + bytes([crc8(header)])
    section = header + encode_log_array(block_offsets) + bytes(buf) + crc32c(bytes(buf)).to_bytes(4, "little")
    return section, len(buf)


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


@dataclass(frozen=True)
class DictionaryIds:
    """ID lookup for a just-built ``FourSectionDictionary`` — the inverse
    of ``FourSectionDictionary.subject_string``/``object_string``/
    ``predicate_string``, used while encoding triples to ID tuples.
    """

    dictionary: FourSectionDictionary
    _shared: dict[str, int]
    _subjects: dict[str, int]
    _predicates: dict[str, int]
    _objects: dict[str, int]

    def subject_id(self, s: str) -> int:
        return self._shared.get(s) or self._subjects[s]

    def predicate_id(self, p: str) -> int:
        return self._predicates[p]

    def object_id(self, o: str) -> int:
        return self._shared.get(o) or self._objects[o]


def build_four_section_dictionary(triples: list[tuple[str, str, str]]) -> DictionaryIds:
    """Build a FourSectionDictionary (plus its ID lookup) from a triple
    list. Term sets split per DECISIONS.md section 4: terms used as both
    subject and object anywhere go in the shared section; the ID space per
    slot is (shared-section IDs, then subject-only or object-only IDs) —
    every subject/object ID is guaranteed to appear in at least one triple,
    since sections are built only from terms that actually occur.
    """
    subjects_used = {s for s, _p, _o in triples}
    objects_used = {o for _s, _p, o in triples}
    shared_set = subjects_used & objects_used

    shared = sorted(shared_set)
    subjects_only = sorted(subjects_used - shared_set)
    predicates = sorted({p for _s, p, _o in triples})
    objects_only = sorted(objects_used - shared_set)

    shared_ids = {s: i + 1 for i, s in enumerate(shared)}
    subject_ids = {s: i + 1 + len(shared) for i, s in enumerate(subjects_only)}
    predicate_ids = {p: i + 1 for i, p in enumerate(predicates)}
    object_ids = {o: i + 1 + len(shared) for i, o in enumerate(objects_only)}

    dictionary = FourSectionDictionary(shared, subjects_only, predicates, objects_only)
    return DictionaryIds(dictionary, shared_ids, subject_ids, predicate_ids, object_ids)


def encode_four_section_dictionary(d: FourSectionDictionary) -> bytes:
    """Inverse of ``decode_four_section_dictionary``."""
    shared_bytes, shared_len = encode_pfc_section(d.shared)
    subjects_bytes, subjects_len = encode_pfc_section(d.subjects)
    predicates_bytes, predicates_len = encode_pfc_section(d.predicates)
    objects_bytes, objects_len = encode_pfc_section(d.objects)

    size_strings = shared_len + subjects_len + predicates_len + objects_len
    control = build_control_info(
        3, "<http://purl.org/HDT/hdt#dictionaryFour>", f"mapping=1;sizeStrings={size_strings};"
    )
    return control + shared_bytes + subjects_bytes + predicates_bytes + objects_bytes
