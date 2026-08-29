"""
FourSectionDictionary with lookups that do not expand the dictionary.

An HDT dictionary is four Plain-Front-Coding sections -- shared, subjects,
predicates, objects -- each a list of byte-lexicographically sorted strings
cut into fixed-size blocks. The first string of every block is stored whole;
the rest are front-coded against their predecessor.

That gives two operations without decoding the section:

* ``id_of(term)`` binary-searches the block offsets on each block's whole
  first string, then front-decodes one block (16 strings).
* ``string_at(id)`` jumps to block ``(id - 1) // blocksize`` and decodes only
  up to that entry.

Both touch one block. Decoding every string, which is what a bulk reader does,
is what makes a query engine load the file it was supposed to seek into.
"""

from __future__ import annotations

from typing import Iterator, Optional

from .binio import LogArray, _check_crc32c, crc8, vbyte_decode
from .header import DICTIONARY, parse_control_info


class PFCSection:
    """One Plain-Front-Coding section, read in place."""

    __slots__ = ("data", "num_strings", "blocksize", "_offsets", "_buf", "end", "_crc_offset")

    def __init__(self, data, pos: int) -> None:
        start = pos
        pos += 1                                    # type byte
        self.num_strings, pos = vbyte_decode(data, pos)
        nbytes, pos = vbyte_decode(data, pos)
        self.blocksize, pos = vbyte_decode(data, pos)
        if crc8(data[start:pos]) != data[pos]:
            raise ValueError(f"PFC section header CRC8 mismatch at offset {start}")
        pos += 1

        self._offsets = LogArray(data, pos)          # block starts + end sentinel
        pos = self._offsets.end

        self.data = data
        self._buf = pos                              # file offset of the string buffer
        self._crc_offset = pos + nbytes
        self.end = self._crc_offset + 4

    def __len__(self) -> int:
        return self.num_strings

    @property
    def num_blocks(self) -> int:
        return max(len(self._offsets) - 1, 0)

    # -- reading one block -------------------------------------------------

    def _first_of_block(self, block: int) -> bytes:
        """The block's first string, stored whole -- the binary-search key."""
        start = self._buf + self._offsets[block]
        end = self.data.find(b"\x00", start)
        return bytes(self.data[start:end])

    def _walk_block(self, block: int, limit: Optional[int] = None) -> Iterator[bytes]:
        """
        Front-decode a block, yielding up to ``limit`` strings.

        Shared-prefix lengths count *bytes*, not characters -- slicing decoded
        text here would corrupt any IRI with a multi-byte character in it.
        """
        pos = self._buf + self._offsets[block]
        count = min(self.blocksize, self.num_strings - block * self.blocksize)
        if limit is not None:
            count = min(count, limit)
        previous = b""
        for i in range(count):
            shared = 0
            if i:
                shared, pos = vbyte_decode(self.data, pos)
            end = self.data.find(b"\x00", pos)
            raw = previous[:shared] + bytes(self.data[pos:end])
            pos = end + 1
            yield raw
            previous = raw

    # -- the two public lookups -------------------------------------------

    def string_at(self, id_: int) -> str:
        """The string with 1-based ID ``id_``. Decodes one block."""
        if not 1 <= id_ <= self.num_strings:
            raise IndexError(f"id {id_} out of range for section of {self.num_strings}")
        block, within = divmod(id_ - 1, self.blocksize)
        raw = b""
        for raw in self._walk_block(block, limit=within + 1):
            pass
        return raw.decode("utf-8")

    def id_of(self, term: str) -> Optional[int]:
        """
        1-based ID of ``term``, or None if absent.

        None is the useful answer: a triple pattern naming a term this file
        has never seen is answered with zero triples read.
        """
        if not self.num_strings:
            return None
        needle = term.encode("utf-8")

        lo, hi = 0, self.num_blocks - 1
        if needle < self._first_of_block(0):
            return None
        while lo < hi:                               # last block whose head <= needle
            mid = (lo + hi + 1) // 2
            if self._first_of_block(mid) <= needle:
                lo = mid
            else:
                hi = mid - 1

        for offset, raw in enumerate(self._walk_block(lo)):
            if raw == needle:
                return lo * self.blocksize + offset + 1
            if raw > needle:                         # sorted: no later match possible
                break
        return None

    def __iter__(self) -> Iterator[str]:
        """Every string in ID order. For inspection, not for the query path."""
        for block in range(self.num_blocks):
            for raw in self._walk_block(block):
                yield raw.decode("utf-8")

    def verify(self) -> None:
        self._offsets.verify()
        _check_crc32c(self.data, self._buf, self._crc_offset, "PFC string buffer")


class FourSectionDictionary:
    """
    The four sections plus HDT's ID conventions.

    Terms used as both subject and object live in ``shared`` and take the low
    IDs in *both* the subject and object spaces; subject-only and object-only
    terms are numbered after them. Predicates have their own space.
    """

    __slots__ = ("shared", "subjects", "predicates", "objects", "end")

    def __init__(self, data, pos: int) -> None:
        info, pos = parse_control_info(data, pos)
        if info.control_type != DICTIONARY:
            raise ValueError(f"expected a dictionary block, got type {info.control_type}")
        if "dictionaryFour" not in info.format:
            raise ValueError(
                f"unsupported dictionary format {info.format!r} "
                "(only FourSectionDictionary is implemented)"
            )
        self.shared = PFCSection(data, pos)
        self.subjects = PFCSection(data, self.shared.end)
        self.predicates = PFCSection(data, self.subjects.end)
        self.objects = PFCSection(data, self.predicates.end)
        self.end = self.objects.end

    # -- term -> id --------------------------------------------------------

    def subject_id(self, term: str) -> Optional[int]:
        found = self.shared.id_of(term)
        if found is not None:
            return found
        found = self.subjects.id_of(term)
        return None if found is None else found + len(self.shared)

    def object_id(self, term: str) -> Optional[int]:
        found = self.shared.id_of(term)
        if found is not None:
            return found
        found = self.objects.id_of(term)
        return None if found is None else found + len(self.shared)

    def predicate_id(self, term: str) -> Optional[int]:
        return self.predicates.id_of(term)

    # -- id -> term --------------------------------------------------------

    def subject_string(self, id_: int) -> str:
        shared = len(self.shared)
        return self.shared.string_at(id_) if id_ <= shared else self.subjects.string_at(id_ - shared)

    def object_string(self, id_: int) -> str:
        shared = len(self.shared)
        return self.shared.string_at(id_) if id_ <= shared else self.objects.string_at(id_ - shared)

    def predicate_string(self, id_: int) -> str:
        return self.predicates.string_at(id_)

    def verify(self) -> None:
        for section in (self.shared, self.subjects, self.predicates, self.objects):
            section.verify()

    def __repr__(self) -> str:
        return (f"<FourSectionDictionary shared={len(self.shared)} "
                f"subjects={len(self.subjects)} predicates={len(self.predicates)} "
                f"objects={len(self.objects)}>")
