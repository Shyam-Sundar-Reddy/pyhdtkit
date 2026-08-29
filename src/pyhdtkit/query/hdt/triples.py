"""
BitmapTriples, seekable by subject.

The triples section is an adjacency list in SPO order, and no subject ID is
ever written down:

* ``ArrayY`` holds one predicate ID per (S,P) pair; ``BitmapY`` sets a bit on
  the *last* pair of each subject.
* ``ArrayZ`` holds one object ID per triple; ``BitmapZ`` sets a bit on the
  *last* object of each (S,P) pair.

Reading it front to back -- counting bits as you go -- is how a bulk decoder
recovers the subjects, and it costs the whole file. But those bitmaps are
exactly a rank/select structure: the k-th set bit of BitmapY *is* the end of
subject k. So ``select1`` jumps straight to a subject's pairs, and ``rank1``
names the subject at any pair. That is the difference between scanning a file
and seeking into it.

Only ``order=1`` (SPO) is read. Any other order would place IDs in different
slots, so it raises rather than silently returning wrong triples.
"""

from __future__ import annotations

from typing import Iterator, Optional

from .binio import Bitmap, LogArray
from .header import TRIPLES, parse_control_info

IdTriple = tuple[int, int, int]


class BitmapTriples:
    """The triples section, read in place."""

    __slots__ = ("bitmap_y", "bitmap_z", "array_y", "array_z", "end")

    def __init__(self, data, pos: int) -> None:
        info, pos = parse_control_info(data, pos)
        if info.control_type != TRIPLES:
            raise ValueError(f"expected a triples block, got type {info.control_type}")
        if "triplesBitmap" not in info.format:
            raise ValueError(
                f"unsupported triples format {info.format!r} "
                "(only BitmapTriples is implemented)"
            )
        order = info.properties.get("order")
        if order != "1":
            raise NotImplementedError(
                f"only SPO triple order (order=1) is implemented, got order={order!r}"
            )

        self.bitmap_y = Bitmap(data, pos)
        self.bitmap_z = Bitmap(data, self.bitmap_y.end)
        self.array_y = LogArray(data, self.bitmap_z.end)
        self.array_z = LogArray(data, self.array_y.end)
        self.end = self.array_z.end

    @property
    def num_triples(self) -> int:
        return len(self.array_z)

    @property
    def num_subjects(self) -> int:
        return self.bitmap_y.count_ones

    # -- navigation --------------------------------------------------------

    def subject_of(self, y: int) -> int:
        """Which subject owns pair ``y``. One rank query, no scan."""
        return self.bitmap_y.rank1(y) + 1

    def pairs_of_subject(self, subject_id: int) -> tuple[int, int]:
        """
        Half-open range of ArrayY indices belonging to ``subject_id``.

        Subject k's pairs end at the k-th set bit of BitmapY and begin just
        after the (k-1)-th -- two select queries, each touching one 512-bit
        block, whatever the file's size.
        """
        end = self.bitmap_y.select1(subject_id)
        if end < 0:
            return 0, 0                              # no such subject in this file
        start = 0 if subject_id == 1 else self.bitmap_y.select1(subject_id - 1) + 1
        return start, end + 1

    def objects_of_pair(self, y: int) -> tuple[int, int]:
        """Half-open range of ArrayZ indices belonging to pair ``y``."""
        end = self.bitmap_z.select1(y + 1)
        if end < 0:
            return 0, 0
        start = 0 if y == 0 else self.bitmap_z.select1(y) + 1
        return start, end + 1

    # -- pattern search ----------------------------------------------------

    def search(
        self,
        subject_id: Optional[int] = None,
        predicate_id: Optional[int] = None,
        object_id: Optional[int] = None,
    ) -> Iterator[IdTriple]:
        """
        Yield matching ID triples; ``None`` is a wildcard.

        A bound subject seeks -- only that subject's pairs are visited. An
        unbound subject walks the pair list, which is the best SPO order
        allows: HDT indexes subjects, so (?, p, ?) and (?, ?, o) are scans.
        They stay cheap because the walk compares packed integers and never
        touches the dictionary; strings are resolved by the caller, only for
        triples that actually matched.

        ponytail: those two patterns are O(pairs) and O(triples) per file.
        The standard fix is HDT-FoQ's extra PSO/OPS indexes, which live in a
        separate .index file this reader does not read. The per-file catalog
        (which predicates a file contains) prunes most of it at a coarser
        level for far less work.
        """
        if subject_id is not None:
            y_start, y_stop = self.pairs_of_subject(subject_id)
            if y_start == y_stop:
                return
            subject = subject_id
        else:
            y_start, y_stop = 0, len(self.array_y)
            subject = 1

        array_y, array_z = self.array_y, self.array_z
        bitmap_y, bitmap_z = self.bitmap_y, self.bitmap_z

        # Objects for pair y start where pair y-1 ended, so track z instead of
        # paying a select per pair.
        z = 0 if y_start == 0 else bitmap_z.select1(y_start) + 1

        for y in range(y_start, y_stop):
            predicate = array_y[y]
            z_start = z
            while not bitmap_z[z]:                   # advance to end of this run
                z += 1
            z_stop = z + 1
            z = z_stop

            if predicate_id is None or predicate == predicate_id:
                for index in range(z_start, z_stop):
                    obj = array_z[index]
                    if object_id is None or obj == object_id:
                        yield subject, predicate, obj

            if subject_id is None and bitmap_y[y]:
                subject += 1

    def predicate_counts(self) -> dict[int, int]:
        """
        Triple count per predicate ID, without decoding a single triple.

        Walks the pair list and measures each object run by its bitmap
        boundaries -- integers only, no dictionary lookups and no object
        reads. This is what makes building a catalog cheap enough to be
        worth it.
        """
        counts: dict[int, int] = {}
        bitmap_z, array_y = self.bitmap_z, self.array_y
        z = 0
        for y in range(len(array_y)):
            start = z
            while not bitmap_z[z]:
                z += 1
            z += 1
            predicate = array_y[y]
            counts[predicate] = counts.get(predicate, 0) + (z - start)
        return counts

    def __iter__(self) -> Iterator[IdTriple]:
        return self.search()

    def verify(self) -> None:
        for part in (self.bitmap_y, self.bitmap_z, self.array_y, self.array_z):
            part.verify()

    def __repr__(self) -> str:
        return f"<BitmapTriples {self.num_triples} triples, {self.num_subjects} subjects>"
