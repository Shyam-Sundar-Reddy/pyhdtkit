"""
Build catalog statistics by reading each HDT file's metadata sections.

Nothing here decodes triples. Triple counts and distinct subject/object counts
come straight from section headers; predicate counts come from a walk of the
packed pair list, comparing integers only. That is what keeps a rebuild cheap
enough to be worth caching.
"""

from __future__ import annotations

import logging

from ..hdt import HDTFile
from ..mapping import Mapping
from .models import FileStats, UrnStats

log = logging.getLogger(__name__)


class CatalogBuilder:
    """Computes :class:`UrnStats` for the URNs of a :class:`Mapping`."""

    def __init__(self, mapping: Mapping) -> None:
        self.mapping = mapping
        self._base = mapping.source.parent

    def build(self, urn: str) -> UrnStats:
        """Statistics for one URN, reading each of its files' headers."""
        return UrnStats(urn, [self._file_stats(path) for path in self.mapping.files_for(urn)])

    def build_all(self) -> dict[str, UrnStats]:
        """Statistics for every URN in the mapping."""
        stats = {urn: self.build(urn) for urn in self.mapping.urns()}
        log.info("catalogued %d urns / %d files",
                 len(stats), sum(len(s.files) for s in stats.values()))
        return stats

    def _file_stats(self, path) -> FileStats:
        stat = path.stat()
        with HDTFile(path) as hdt:
            subjects, objects = hdt.term_counts()
            return FileStats(
                path=self._relative(path),
                size=stat.st_size,
                mtime=stat.st_mtime,
                triple_count=len(hdt),
                subject_count=subjects,
                object_count=objects,
                predicates=hdt.predicate_counts(),
            )

    def _relative(self, path) -> str:
        """Store paths the way the mapping does -- relative to it, so the
        cache survives the whole tree being moved."""
        try:
            return path.relative_to(self._base).as_posix()
        except ValueError:
            return path.as_posix()
