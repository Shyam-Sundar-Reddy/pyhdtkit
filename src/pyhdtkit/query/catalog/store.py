"""
The catalog itself: cached statistics, with staleness detection and rebuild.

The cache is a JSON file next to the mapping. It exists so a process start
does not re-read every HDT file, and it is validated on load -- a file whose
size or mtime has moved is rebuilt rather than trusted, because stale
statistics would silently prune away files that do hold matches.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..mapping import Mapping
from .builder import CatalogBuilder
from .models import FileStats, UrnStats

log = logging.getLogger(__name__)

CACHE_SUFFIX = ".catalog.json"
CACHE_VERSION = 1


class Catalog:
    """
    Per-URN and per-file statistics, cached on disk.

        catalog = Catalog.load(mapping)
        catalog.stats_for("urn:hdt:bian").predicates
        catalog.rebuild("urn:hdt:bian")
    """

    def __init__(self, mapping: Mapping, stats: dict[str, UrnStats],
                 cache_path: Path, built_at: Optional[str] = None) -> None:
        self.mapping = mapping
        self.cache_path = cache_path
        self.built_at = built_at or _now()
        self._stats = stats
        self._builder = CatalogBuilder(mapping)

    # -- construction ------------------------------------------------------

    @classmethod
    def default_cache_path(cls, mapping: Mapping) -> Path:
        return mapping.source.with_suffix(CACHE_SUFFIX)

    @classmethod
    def load(cls, mapping: Mapping, cache_path: Optional[Path] = None,
             save: bool = True) -> Catalog:
        """
        Load the cached catalog, rebuilding whatever is missing or stale.

        Never raises on a damaged or outdated cache -- a cache is an
        optimisation, so a bad one is discarded and rebuilt, not fatal.
        """
        cache_path = Path(cache_path) if cache_path else cls.default_cache_path(mapping)
        cached, built_at = cls._read_cache(cache_path, mapping)

        stats: dict[str, UrnStats] = {}
        rebuilt = []
        base = mapping.source.parent
        builder = CatalogBuilder(mapping)
        for urn in mapping.urns():
            entry = cached.get(urn)
            if entry is not None and cls._is_current(entry, mapping, base):
                stats[urn] = entry
            else:
                stats[urn] = builder.build(urn)
                rebuilt.append(urn)

        catalog = cls(mapping, stats, cache_path, None if rebuilt else built_at)
        if rebuilt:
            log.info("catalog rebuilt for %d urn(s): %s", len(rebuilt), ", ".join(rebuilt))
            if save:
                catalog.save()
        return catalog

    @staticmethod
    def _read_cache(path: Path, mapping: Mapping) -> tuple[dict[str, UrnStats], Optional[str]]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("version") != CACHE_VERSION:
                return {}, None
            return ({u: UrnStats.from_json(d) for u, d in doc["urns"].items()},
                    doc.get("built_at"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.debug("ignoring unusable catalog cache %s: %s", path, exc)
            return {}, None

    @staticmethod
    def _is_current(entry: UrnStats, mapping: Mapping, base: Path) -> bool:
        """Cached entry is usable only if it lists exactly today's files and
        none of them has changed on disk."""
        expected = [p.relative_to(base).as_posix() if p.is_relative_to(base) else p.as_posix()
                    for p in mapping.files_for(entry.urn)]
        if [f.path for f in entry.files] != expected:
            return False
        return all(f.is_current(base) for f in entry.files)

    # -- querying ----------------------------------------------------------

    def stats_for(self, urn: str) -> UrnStats:
        if urn not in self._stats:
            raise KeyError(f"URN not in catalog: {urn!r}")
        return self._stats[urn]

    def urns(self) -> list[str]:
        return list(self._stats)

    def triple_count(self, urn: Optional[str] = None) -> int:
        if urn is not None:
            return self.stats_for(urn).triple_count
        return sum(s.triple_count for s in self._stats.values())

    def files_with_predicate(self, urn: str, predicate: Optional[str]) -> Optional[list[str]]:
        """
        Which of ``urn``'s files can match a pattern using ``predicate``.

        ``None`` in, ``None`` out: an unbound predicate prunes nothing, and
        the caller should use every file. Otherwise this is the pruning list,
        possibly empty when no file holds that predicate at all.
        """
        if predicate is None:
            return None
        return self.stats_for(urn).files_with_predicate(predicate)

    def __contains__(self, urn: str) -> bool:
        return urn in self._stats

    def __iter__(self) -> Iterator[UrnStats]:
        return iter(self._stats.values())

    # -- maintenance -------------------------------------------------------

    def rebuild(self, urn: Optional[str] = None, save: bool = True) -> None:
        """Recompute one URN, or all of them, and refresh the cache."""
        targets = [urn] if urn else self.mapping.urns()
        for target in targets:
            if not self.mapping.contains(target):
                raise KeyError(f"URN not in mapping: {target!r}")
            self._stats[target] = self._builder.build(target)
        self.built_at = _now()
        if save:
            self.save()

    def save(self) -> None:
        """Write the cache atomically, so a crash mid-write cannot leave a
        half-written catalog that later loads as truth."""
        doc = {
            "version": CACHE_VERSION,
            "built_at": self.built_at,
            "mapping": str(self.mapping.source),
            "urns": {urn: stats.to_json() for urn, stats in self._stats.items()},
        }
        temp = self.cache_path.with_suffix(".tmp")
        temp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        temp.replace(self.cache_path)

    def __repr__(self) -> str:
        return (f"<Catalog {len(self._stats)} urns, {self.triple_count()} triples, "
                f"built {self.built_at}>")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["Catalog", "CatalogBuilder", "FileStats", "UrnStats", "CACHE_SUFFIX"]
