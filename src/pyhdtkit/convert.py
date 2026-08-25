"""Public conversion API for pyhdtkit.

These functions are the stable Python-facing surface, backed by a from-scratch
pure-Python HDT binary reader/writer (``pyhdtkit.hdt``) and ``rdflib`` for the
Turtle side (``pyhdtkit.ttl``). ``hdtcat`` is still a stub.
"""

from __future__ import annotations

from pathlib import Path

from pyhdtkit.hdt.reader import read_hdt, write_hdt
from pyhdtkit.ttl import parse_ttl, serialize_ttl


def ttl2hdt(
    input_path: str | Path,
    output_path: str | Path,
    *,
    base_uri: str | None = None,
) -> None:
    """Convert a Turtle (``.ttl``) file to HDT (``.hdt``).

    Args:
        input_path: Path to the source ``.ttl`` file.
        output_path: Path to write the resulting ``.hdt`` file.
        base_uri: Optional base URI for resolving relative IRIs while parsing.

    Raises:
        ValueError: The ``.ttl`` file could not be parsed or the ``.hdt`` file could
            not be written (e.g. malformed Turtle, or an unwritable output path).
    """
    triples = parse_ttl(input_path, base_uri=base_uri)
    write_hdt(triples, output_path)


def hdt2ttl(
    input_path: str | Path,
    output_path: str | Path,
) -> None:
    """Convert an HDT (``.hdt``) file to Turtle (``.ttl``).

    Args:
        input_path: Path to the source ``.hdt`` file.
        output_path: Path to write the resulting ``.ttl`` file.

    Raises:
        ValueError: The ``.hdt`` file could not be read or the ``.ttl`` file could
            not be written (e.g. a malformed HDT file, or an unwritable output path).
    """
    triples = read_hdt(input_path)
    serialize_ttl(triples, output_path)


def hdtcat(
    input_paths: list[str | Path],
    output_path: str | Path,
) -> None:
    """Combine two or more ``.hdt`` files into a single ``.hdt``, de-duplicating triples.

    Args:
        input_paths: Paths of the ``.hdt`` files to merge (2 or more).
        output_path: Path to write the merged ``.hdt`` file.

    Raises:
        ValueError: Fewer than 2 input paths were given, or an input ``.hdt`` could
            not be read, or the ``.hdt`` file could not be written.
    """
    if len(input_paths) < 2:
        raise ValueError("hdtcat requires at least 2 input .hdt files")
    raise NotImplementedError("hdtcat is not implemented yet")
