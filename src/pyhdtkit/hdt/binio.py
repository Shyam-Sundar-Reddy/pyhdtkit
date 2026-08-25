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


def crc32c_python(data: bytes) -> int:
    """Pure-Python CRC-32C. Always available; used directly by the tests
    that pin the algorithm against known fixture bytes, and as the fallback
    when the optional accelerator below isn't installed.
    """
    crc = 0xFFFFFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC32C_TABLE[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


try:
    # Optional accelerator (`pip install pyhdtkit[fast]`). CRC-32C is a
    # generic checksum, not HDT logic, so a prebuilt wheel is fair game
    # here — and it's ~2600x faster than the loop above, which is the
    # single largest cost in the read path once everything else is tuned.
    # Verified to produce identical values, including the known fixture
    # checksums the tests pin.
    from google_crc32c import value as _crc32c_accelerated
except ImportError:  # pragma: no cover - depends on optional install
    _crc32c_accelerated = None


def crc32c(data: bytes) -> int:
    if _crc32c_accelerated is not None:
        return _crc32c_accelerated(bytes(data))
    return crc32c_python(data)


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


# Byte value -> its 8 bits, LSB first. Lets the numbits==1 path below expand
# a whole byte per iteration via list.extend (C-level) instead of looping
# once per bit.
_BIT_OCTETS = [tuple((b >> k) & 1 for k in range(8)) for b in range(256)]
_OCTET_VALUE = {bits: value for value, bits in enumerate(_BIT_OCTETS)}


def unpack_lsb_bitfields(data: bytes, numbits: int, count: int) -> list[int]:
    """Unpack ``count`` fields of ``numbits`` bits each, LSB-first — as if
    ``data`` were one little-endian bit-integer sliced into consecutive
    numbits-wide chunks starting from bit 0.

    Streams through a small bit buffer rather than materializing that
    whole-array bit-integer directly (``int.from_bytes`` then repeated
    shifts): Python ints are immutable, so building one covering the whole
    array and shifting it per entry is O(n^2) for large arrays — measured
    ~1s of pure overhead at 100k entries alone (see benchmarks/bench.py).
    This is O(n): the buffer never holds more than one machine word's worth
    of bits.
    """
    if numbits == 1:
        # BitmapY/BitmapZ take this path, and they're the largest arrays in
        # a typical file (one bit per triple) — ~7x faster than the general
        # loop below by expanding a byte at a time.
        values: list[int] = []
        extend = values.extend
        for byte in data:
            extend(_BIT_OCTETS[byte])
        del values[count:]  # trailing padding bits in the final byte
        return values
    if numbits == 8:
        return list(data[:count])

    mask = (1 << numbits) - 1
    values = []
    buffer = 0
    bits_in_buffer = 0
    byte_pos = 0
    for _ in range(count):
        while bits_in_buffer < numbits:
            buffer |= data[byte_pos] << bits_in_buffer
            byte_pos += 1
            bits_in_buffer += 8
        values.append(buffer & mask)
        buffer >>= numbits
        bits_in_buffer -= numbits
    return values


def pack_lsb_bitfields(values: list[int], numbits: int) -> bytes:
    """Inverse of ``unpack_lsb_bitfields`` — same O(n) streaming approach."""
    if numbits == 1:
        # Mirror of the numbits==1 fast path in unpack: consume 8 bits per
        # iteration via a tuple->byte lookup instead of one shift per bit.
        n = len(values)
        whole = n - (n % 8)
        out = bytearray()
        append = out.append
        octet_value = _OCTET_VALUE
        for i in range(0, whole, 8):
            append(octet_value[tuple(values[i : i + 8])])
        if whole < n:
            last = 0
            for k in range(whole, n):
                if values[k]:
                    last |= 1 << (k - whole)
            append(last)
        return bytes(out)

    out = bytearray()
    buffer = 0
    bits_in_buffer = 0
    for v in values:
        buffer |= v << bits_in_buffer
        bits_in_buffer += numbits
        while bits_in_buffer >= 8:
            out.append(buffer & 0xFF)
            buffer >>= 8
            bits_in_buffer -= 8
    if bits_in_buffer > 0:
        out.append(buffer & 0xFF)
    return bytes(out)


def bits_needed(max_value: int) -> int:
    """Minimum bit width to represent ``max_value`` (at least 1, so a
    single-entry array of value 0 still has a defined width).
    """
    return max(1, max_value.bit_length())


def encode_log_array(values: list[int]) -> bytes:
    """Inverse of ``decode_log_array``."""
    numbits = bits_needed(max(values, default=0))
    header = bytes([1, numbits]) + vbyte_encode(len(values))
    header = header + bytes([crc8(header)])
    packed = pack_lsb_bitfields(values, numbits)
    return header + packed + crc32c(packed).to_bytes(4, "little")


def encode_bitmap(bits: list[int]) -> bytes:
    """Inverse of ``decode_bitmap``."""
    header = bytes([1]) + vbyte_encode(len(bits))
    header = header + bytes([crc8(header)])
    packed = pack_lsb_bitfields(bits, 1)
    return header + packed + crc32c(packed).to_bytes(4, "little")
