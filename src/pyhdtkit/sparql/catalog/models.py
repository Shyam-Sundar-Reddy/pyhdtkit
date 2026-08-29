"""Catalog value types: what we know about a file and a URN without opening them."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileStats:
    """
    Statistics for one ``.hdt`` file.

    ``predicates`` is the reason the catalog exists: it lets the store skip a
    file whose dictionary has never heard of the predicate a pattern names,
    without opening it. ``size`` and ``mtime`` are the staleness check.
    """

    path: str
    size: int
    mtime: float
    triple_count: int
    subject_count: int
    object_count: int
    predicates: dict[str, int] = field(default_factory=dict)

    def is_current(self, base: Path) -> bool:
        """Whether the file on disk still matches what was catalogued."""
        resolved = base / self.path
        try:
            stat = resolved.stat()
        except OSError:
            return False
        return stat.st_size == self.size and stat.st_mtime == self.mtime

    def to_json(self) -> dict:
        return {
            "path": self.path, "size": self.size, "mtime": self.mtime,
            "triple_count": self.triple_count, "subject_count": self.subject_count,
            "object_count": self.object_count, "predicates": self.predicates,
        }

    @classmethod
    def from_json(cls, doc: dict) -> FileStats:
        return cls(**doc)


@dataclass(frozen=True)
class UrnStats:
    """
    Statistics for one URN, aggregated over its files.

    ``triple_count`` sums the files, so it counts a triple once per file it
    appears in -- it is an upper bound on the graph's distinct triples, not a
    ``COUNT(DISTINCT *)``. Deduplicating would mean reading every triple,
    which is exactly what the catalog exists to avoid.
    """

    urn: str
    files: list[FileStats]

    @property
    def triple_count(self) -> int:
        return sum(f.triple_count for f in self.files)

    @property
    def predicates(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for stats in self.files:
            for predicate, count in stats.predicates.items():
                totals[predicate] = totals.get(predicate, 0) + count
        return totals

    @property
    def subject_count(self) -> int:
        return sum(f.subject_count for f in self.files)

    @property
    def object_count(self) -> int:
        return sum(f.object_count for f in self.files)

    def files_with_predicate(self, predicate: str) -> list[str]:
        """Paths of the files that contain ``predicate`` -- the pruning list."""
        return [f.path for f in self.files if predicate in f.predicates]

    def to_json(self) -> dict:
        return {"urn": self.urn, "files": [f.to_json() for f in self.files]}

    @classmethod
    def from_json(cls, doc: dict) -> UrnStats:
        return cls(doc["urn"], [FileStats.from_json(f) for f in doc["files"]])

    def __repr__(self) -> str:
        return (f"<UrnStats {self.urn}: {len(self.files)} files, "
                f"{self.triple_count} triples, {len(self.predicates)} predicates>")
