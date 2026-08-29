"""Read a mapping file from disk, validate it, and hand back a Mapping."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import Mapping, MappingError

log = logging.getLogger(__name__)

HDT_SUFFIX = ".hdt"


class MappingLoader:
    """
    Loads the JSON mapping described in docs/data_model.md.

        mapping = MappingLoader("map.json").load()

    Validation is deliberately all-or-nothing: a mapping with one bad entry
    raises rather than loading the rest. A silently dropped graph turns a
    broken deployment into wrong query results, which is far worse than a
    crash at startup.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> Mapping:
        """
        Parse, validate, and return the mapping.

        Raises :class:`MappingError` for a missing or unparseable file, a
        non-object document, a URN whose value is not a list of strings, an
        empty file list, a duplicate URN, a path without a ``.hdt`` suffix, or
        a path that is not a file on disk. The message always names the
        offending URN and path.
        """
        raw = self._read()
        entries: dict[str, list[Path]] = {}
        for urn, paths in raw.items():
            entries[urn] = [self._resolve(urn, p) for p in self._paths(urn, paths)]

        mapping = Mapping(entries, self.path)
        log.info("loaded %d urns / %d files from %s", len(mapping),
                 sum(len(v) for v in entries.values()), self.path)
        return mapping

    # -- internals ---------------------------------------------------------

    def _read(self) -> dict[str, object]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MappingError(f"cannot read mapping {self.path}: {exc}") from exc
        try:
            raw = json.loads(text, object_pairs_hook=self._reject_duplicates)
        except json.JSONDecodeError as exc:
            raise MappingError(f"invalid JSON in mapping {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise MappingError(
                f"mapping {self.path} must be a JSON object of urn -> file list, "
                f"got {type(raw).__name__}"
            )
        return raw

    def _reject_duplicates(self, pairs):
        # json.load would silently keep the last value for a repeated key,
        # quietly dropping a graph's files. Refuse instead.
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise MappingError(f"duplicate URN in mapping {self.path}: {key!r}")
            seen[key] = value
        return seen

    def _paths(self, urn: str, paths: object) -> list[str]:
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise MappingError(
                f"mapping {self.path}: URN {urn!r} must map to a list of path "
                f"strings, got {type(paths).__name__}"
            )
        if not paths:
            raise MappingError(f"mapping {self.path}: URN {urn!r} has no files")
        return paths

    def _resolve(self, urn: str, path: str) -> Path:
        # Relative to the mapping file's directory, so mapping and data move
        # together; absolute paths are left alone (see docs/data_model.md).
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = self.path.parent / resolved
        if resolved.suffix != HDT_SUFFIX:
            raise MappingError(
                f"mapping {self.path}: URN {urn!r} -> not an {HDT_SUFFIX} file: {path}"
            )
        if not resolved.is_file():
            raise MappingError(
                f"mapping {self.path}: URN {urn!r} -> missing file: {path} "
                f"(looked in {resolved.parent})"
            )
        return resolved
