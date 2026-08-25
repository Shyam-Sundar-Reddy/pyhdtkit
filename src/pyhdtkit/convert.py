"""Public conversion API for pyhdtkit.

These functions are the stable Python-facing surface, backed by a from-scratch
pure-Python HDT binary reader/writer (``pyhdtkit.hdt``) and ``rdflib`` for the
Turtle side (``pyhdtkit.ttl``).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from pyhdtkit.hdt.reader import read_hdt, write_hdt
from pyhdtkit.ttl import parse_ttl, serialize_ttl


@contextmanager
def _wrap_errors(what: str):
    """Trust-boundary error normalization: whatever goes wrong reading,
    parsing, decoding, or writing (a missing/unreadable file, malformed
    Turtle, a truncated/corrupt HDT file, an unwritable output path) surfaces
    as ``ValueError`` with the failing operation named, per every public
    function's documented contract — instead of the internal exception type
    (``OSError``, ``SyntaxError``, or an ``IndexError``/``UnicodeDecodeError``
    from indexing off the end of a truncated file) leaking through.
    """
    try:
        yield
    except (OSError, SyntaxError, IndexError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"{what}: {e}") from e


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
    with _wrap_errors(f"ttl2hdt({input_path} -> {output_path})"):
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
    with _wrap_errors(f"hdt2ttl({input_path} -> {output_path})"):
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
    with _wrap_errors(f"hdtcat({input_paths} -> {output_path})"):
        merged: set[tuple[str, str, str]] = set()
        for path in input_paths:
            merged.update(read_hdt(path))
        write_hdt(list(merged), output_path)
