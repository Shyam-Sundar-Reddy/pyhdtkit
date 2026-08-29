"""In-memory model of a validated URN -> HDT file mapping."""

from __future__ import annotations

from pathlib import Path


class MappingError(Exception):
    """A mapping file is malformed, or references paths that cannot be used."""


class Mapping:
    """
    A validated, read-only URN -> HDT file mapping.

    Produced by :class:`~pyhdtkit.query.mapping.loader.MappingLoader`, not built
    directly. Every path held here has already been checked to exist and to
    carry a ``.hdt`` extension, so later phases open them without re-checking.
    """

    def __init__(self, entries: dict[str, list[Path]], source: Path) -> None:
        self._entries = {urn: tuple(paths) for urn, paths in entries.items()}
        self._source = source

    @property
    def source(self) -> Path:
        """The mapping file this was loaded from."""
        return self._source

    def urns(self) -> list[str]:
        """Every URN, in mapping-file order."""
        return list(self._entries)

    def files_for(self, urn: str) -> list[Path]:
        """
        Resolved paths of the HDT files backing ``urn``.

        Raises :class:`MappingError` naming the unknown URN; callers that
        expect a miss should ask :meth:`contains` first. A fresh list is
        returned each call, so mutating it cannot corrupt the mapping.
        """
        if urn not in self._entries:
            known = ", ".join(self._entries) or "none"
            raise MappingError(f"URN not in {self._source}: {urn!r} (known: {known})")
        return list(self._entries[urn])

    def contains(self, urn: str) -> bool:
        """Whether ``urn`` is present in the mapping."""
        return urn in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __repr__(self) -> str:
        files = sum(len(paths) for paths in self._entries.values())
        return f"<Mapping {len(self._entries)} urns, {files} files, from {self._source}>"
