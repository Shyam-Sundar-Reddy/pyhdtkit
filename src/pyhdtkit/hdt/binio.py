"""Low-level HDT binary primitives: VByte varints and the three checksum
algorithms HDT files use, confirmed byte-for-byte against a real ``.hdt``
fixture (see DECISIONS.md, sections 2 and 7). None of these match a common
stdlib/library default exactly, which is why each needs its own small
from-scratch implementation:

- VByte: base-128 varint, but with the continuation bit INVERTED relative
  to typical LEB128 — continuation bytes have the high bit clear, and the
  final byte has the high bit set.
- CRC8: plain CRC-8 (poly 0x07, init 0, no reflection) — not size-limited
  enough to be worth a lookup table.
- CRC16: CRC-16/ARC (poly 0x8005 reflected, init 0).
- CRC32: CRC-32C / Castagnoli (poly 0x1EDC6F41 reflected, init/xorout
  0xFFFFFFFF) — NOT the same as ``zlib.crc32``, which implements standard
  CRC-32 and does not match HDT's stored checksums.
"""

from __future__ import annotations


def vbyte_decode(data: bytes, pos: int) -> tuple[int, int]:
    """Decode one VByte varint starting at ``pos``. Returns (value, new_pos)."""
    value = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        if b & 0x80:
            value |= (b & 0x7F) << shift
            return value, pos
        value |= b << shift
        shift += 7


def vbyte_encode(value: int) -> bytes:
    """Encode a non-negative int as a VByte varint."""
    out = bytearray()
    while value > 0x7F:
        out.append(value & 0x7F)
        value >>= 7
    out.append(value | 0x80)
    return bytes(out)


def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _build_crc16_arc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC16_TABLE = _build_crc16_arc_table()


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = (crc >> 8) ^ _CRC16_TABLE[(crc ^ byte) & 0xFF]
    return crc


def _build_crc32c_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC32C_TABLE = _build_crc32c_table()


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC32C_TABLE[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


def decode_log_array(data: bytes, pos: int) -> tuple[list[int], int]:
    """Decode one LogSequence2 array (DECISIONS.md section 5): ``[type]
    [numbits][numentries VByte][CRC8][packed entries][CRC32C]``. Used both
    for a PFC dictionary section's block-start offsets and for a
    BitmapTriples ArrayY/ArrayZ (predicate/object ID lists) — identical
    on-disk shape in both places.
    """
    start = pos
    _type = data[pos]
    pos += 1
    numbits = data[pos]
    pos += 1
    numentries, pos = vbyte_decode(data, pos)
    header_end = pos
    stored_crc8 = data[pos]
    pos += 1
    if crc8(data[start:header_end]) != stored_crc8:
        raise ValueError(f"array header CRC8 mismatch at offset {start}")

    nbytes = (numbits * numentries + 7) // 8
    packed = data[pos : pos + nbytes]
    pos += nbytes
    stored_crc32 = int.from_bytes(data[pos : pos + 4], "little")
    pos += 4
    if crc32c(packed) != stored_crc32:
        raise ValueError(f"array data CRC32C mismatch at offset {start}")

    return unpack_lsb_bitfields(packed, numbits, numentries), pos


def decode_bitmap(data: bytes, pos: int) -> tuple[list[int], int]:
    """Decode one bitmap (DECISIONS.md section 7b): same shape as
    ``decode_log_array`` but carrying a bit count instead of an entry
    count, and no separate numbits byte (each entry is 1 bit by
    definition): ``[type][VByte totalbits][CRC8][packed bits][CRC32C]``.
    Used for BitmapTriples' BitmapY/BitmapZ.
    """
    start = pos
    _type = data[pos]
    pos += 1
    totalbits, pos = vbyte_decode(data, pos)
    header_end = pos
    stored_crc8 = data[pos]
    pos += 1
    if crc8(data[start:header_end]) != stored_crc8:
        raise ValueError(f"bitmap header CRC8 mismatch at offset {start}")

    nbytes = (totalbits + 7) // 8
    packed = data[pos : pos + nbytes]
    pos += nbytes
    stored_crc32 = int.from_bytes(data[pos : pos + 4], "little")
    pos += 4
    if crc32c(packed) != stored_crc32:
        raise ValueError(f"bitmap data CRC32C mismatch at offset {start}")

    return unpack_lsb_bitfields(packed, 1, totalbits), pos


def unpack_lsb_bitfields(data: bytes, numbits: int, count: int) -> list[int]:
    """Unpack ``count`` fields of ``numbits`` bits each, LSB-first: treat
    ``data`` as one little-endian bit-integer and slice out consecutive
    numbits-wide chunks starting from bit 0.
    """
    total = int.from_bytes(data, "little")
    mask = (1 << numbits) - 1
    return [(total >> (i * numbits)) & mask for i in range(count)]


def pack_lsb_bitfields(values: list[int], numbits: int) -> bytes:
    """Inverse of ``unpack_lsb_bitfields``."""
    total = 0
    for i, v in enumerate(values):
        total |= v << (i * numbits)
    nbytes = (numbits * len(values) + 7) // 8
    return total.to_bytes(nbytes, "little")
