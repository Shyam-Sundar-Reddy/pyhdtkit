"""
Low-level HDT binary primitives, with random access rather than bulk decode.

The point of this module is what it does *not* do: it never expands a packed
array or bitmap into a Python list. ``LogArray`` reads entry *i* by bit
arithmetic against the packed bytes, and ``Bitmap`` answers rank/select from a
small block index. Both let the layers above seek into a file instead of
decoding it.

ponytail: `vbyte_decode`, `crc8`, `crc16` and `crc32c` below duplicate the
same-named functions in ``pyhdtkit.hdt.binio``. This is deliberate, not an
oversight — do not collapse the two.

Reasons: (1) conversion and querying are independent concerns, and an edit to
the conversion decoder must not be able to break querying; (2) they cannot
fully share anyway — ``mmap`` objects have no ``.index()`` method, which the
conversion-side control-info parser relies on, so the query side must parse
against a raw mmap using its own byte loop (see ``header.py``).

Upgrade path: extract a shared zero-dependency primitives module *only* if the
mmap incompatibility is ever resolved on the conversion side. Until then the
~80 duplicated lines are the price of the isolation boundary, which is enforced
by a test (``tests/test_query_isolation.py``).
"""

from __future__ import annotations

# Bits per block in the rank index. 512 bits = 64 bytes per counter, so the
# index costs ~1.6% of the bitmap's own size.
RANK_BLOCK_BITS = 512
RANK_BLOCK_BYTES = RANK_BLOCK_BITS // 8

_POPCOUNT = bytes(bin(b).count("1") for b in range(256))


def vbyte_decode(data, pos: int) -> tuple[int, int]:
    """One VByte varint at ``pos`` -> (value, next_pos).

    HDT inverts the usual LEB128 convention: continuation bytes have the high
    bit *clear* and the final byte has it *set*.
    """
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        if byte & 0x80:
            return value | ((byte & 0x7F) << shift), pos
        value |= byte << shift
        shift += 7


def crc8(data) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _table(poly: int) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC16_TABLE = _table(0xA001)          # CRC-16/ARC
_CRC32C_TABLE = _table(0x82F63B78)     # CRC-32C (Castagnoli), not zlib's CRC-32


def crc16(data) -> int:
    crc = 0
    for byte in data:
        crc = (crc >> 8) ^ _CRC16_TABLE[(crc ^ byte) & 0xFF]
    return crc


def crc32c(data) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC32C_TABLE[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


class LogArray:
    """
    A LogSequence2 array: ``numentries`` fields of ``numbits`` bits each,
    packed LSB-first, read in place.

    On-disk: ``[type][numbits][numentries vbyte][crc8][packed][crc32c]``.
    Random access is what makes subject seeking possible -- ArrayZ holds one
    object ID per triple, and we want entry 4,000,000 without the 3,999,999
    before it.
    """

    __slots__ = ("data", "offset", "numbits", "count", "end", "_mask", "_crc_offset")

    def __init__(self, data, pos: int) -> None:
        start = pos
        pos += 1                                    # type byte
        self.numbits = data[pos]
        pos += 1
        self.count, pos = vbyte_decode(data, pos)
        _check_crc8(data, start, pos, "array header")
        pos += 1

        self.data = data
        self.offset = pos
        self._mask = (1 << self.numbits) - 1
        nbytes = (self.numbits * self.count + 7) // 8
        self._crc_offset = pos + nbytes
        self.end = self._crc_offset + 4

    def __len__(self) -> int:
        return self.count

    def __getitem__(self, index: int) -> int:
        if not 0 <= index < self.count:
            raise IndexError(index)
        bit = index * self.numbits
        byte = self.offset + (bit >> 3)
        # Read enough whole bytes to cover the field regardless of alignment.
        span = (self.numbits + (bit & 7) + 7) // 8
        word = int.from_bytes(self.data[byte:byte + span], "little")
        return (word >> (bit & 7)) & self._mask

    def __iter__(self):
        return (self[i] for i in range(self.count))

    def verify(self) -> None:
        _check_crc32c(self.data, self.offset, self._crc_offset, "array data")


class Bitmap:
    """
    A packed bit array with rank and select.

    ``rank1(i)`` counts set bits below *i*; ``select1(k)`` finds the k-th set
    bit (1-based). BitmapTriples marks run boundaries with set bits, so select
    is literally "jump to subject k's triples".

    The rank index is built once on construction by popcounting bytes -- a
    C-level pass over the packed bytes, no per-bit Python -- and costs one int
    per 512 bits.
    """

    __slots__ = ("data", "offset", "total_bits", "end", "_ranks", "_crc_offset")

    def __init__(self, data, pos: int) -> None:
        start = pos
        pos += 1                                    # type byte
        self.total_bits, pos = vbyte_decode(data, pos)
        _check_crc8(data, start, pos, "bitmap header")
        pos += 1

        self.data = data
        self.offset = pos
        nbytes = (self.total_bits + 7) // 8
        self._crc_offset = pos + nbytes
        self.end = self._crc_offset + 4

        packed = data[pos:pos + nbytes]
        self._ranks = [0]
        running = 0
        for block in range(0, nbytes, RANK_BLOCK_BYTES):
            running += sum(_POPCOUNT[b] for b in packed[block:block + RANK_BLOCK_BYTES])
            self._ranks.append(running)

    def __len__(self) -> int:
        return self.total_bits

    def __getitem__(self, index: int) -> int:
        return (self.data[self.offset + (index >> 3)] >> (index & 7)) & 1

    @property
    def count_ones(self) -> int:
        return self._ranks[-1]

    def rank1(self, index: int) -> int:
        """Number of set bits strictly below ``index``."""
        if index <= 0:
            return 0
        index = min(index, self.total_bits)
        block, rest = divmod(index, RANK_BLOCK_BITS)
        total = self._ranks[block]
        base = self.offset + block * RANK_BLOCK_BYTES
        whole, spare = divmod(rest, 8)
        for i in range(whole):
            total += _POPCOUNT[self.data[base + i]]
        if spare:
            total += _POPCOUNT[self.data[base + whole] & ((1 << spare) - 1)]
        return total

    def select1(self, k: int) -> int:
        """
        Position of the ``k``-th set bit, 1-based. Returns -1 if there are
        fewer than *k* set bits.

        Binary search over the block index, then a byte walk inside one
        512-bit block -- so this touches ~64 bytes, not the whole bitmap.
        """
        if k <= 0 or k > self.count_ones:
            return -1
        lo, hi = 0, len(self._ranks) - 1
        while lo < hi - 1:                          # last block with rank < k
            mid = (lo + hi) // 2
            if self._ranks[mid] < k:
                lo = mid
            else:
                hi = mid
        remaining = k - self._ranks[lo]
        byte = self.offset + lo * RANK_BLOCK_BYTES
        while True:
            ones = _POPCOUNT[self.data[byte]]
            if ones >= remaining:
                bits = self.data[byte]
                for offset in range(8):
                    if (bits >> offset) & 1:
                        remaining -= 1
                        if remaining == 0:
                            return (byte - self.offset) * 8 + offset
            remaining -= ones
            byte += 1

    def verify(self) -> None:
        _check_crc32c(self.data, self.offset, self._crc_offset, "bitmap data")


def _check_crc8(data, start: int, end: int, what: str) -> None:
    if crc8(data[start:end]) != data[end]:
        raise ValueError(f"{what} CRC8 mismatch at offset {start}")


def _check_crc32c(data, start: int, end: int, what: str) -> None:
    stored = int.from_bytes(data[end:end + 4], "little")
    if crc32c(data[start:end]) != stored:
        raise ValueError(f"{what} CRC32C mismatch at offset {start}")
