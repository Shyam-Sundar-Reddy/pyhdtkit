"""
HDT container framing: the Control Information blocks and the RDF header.

Every section of an HDT file is introduced by a Control Information block --
cookie, type, format IRI, properties, CRC16. This module reads those and walks
past the header section, leaving the caller positioned at the dictionary.

ponytail: ``ControlInfo`` and ``parse_control_info`` here duplicate
``pyhdtkit.hdt.control_info``. Deliberate, and in this case unavoidable: the
conversion-side parser locates the null terminators with ``data.index(b"\\x00",
pos)``, and ``mmap`` objects have no ``.index()`` method at all (only
``.find()``). ``_read_cstring`` below walks the bytes by hand so the same code
works against a raw mmap, which is what lets a query parse a file's framing
without reading it into memory. See the module docstring in ``binio.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .binio import crc16, vbyte_decode

COOKIE = b"$HDT"
GLOBAL, HEADER, DICTIONARY, TRIPLES = 1, 2, 3, 4


@dataclass(frozen=True)
class ControlInfo:
    control_type: int
    format: str
    properties: dict[str, str]


def parse_control_info(data, pos: int) -> tuple[ControlInfo, int]:
    """
    One Control Information block at ``pos`` -> (info, next_pos).

    Layout: ``"$HDT"``, one type byte, a null-terminated format string, a
    null-terminated ``key=value;`` property string, then a CRC16 over
    everything from the cookie through that final null.
    """
    start = pos
    if bytes(data[pos:pos + 4]) != COOKIE:
        raise ValueError(
            f"not an HDT section at offset {pos}: expected {COOKIE!r}, "
            f"found {bytes(data[pos:pos + 4])!r}"
        )
    pos += 4
    control_type = data[pos]
    pos += 1

    format_string, pos = _read_cstring(data, pos)
    properties, pos = _read_cstring(data, pos)

    stored = int.from_bytes(data[pos:pos + 2], "little")
    if crc16(data[start:pos]) != stored:
        raise ValueError(f"control info CRC16 mismatch at offset {start}")
    pos += 2

    return ControlInfo(control_type, format_string, _properties(properties)), pos


def _read_cstring(data, pos: int) -> tuple[str, int]:
    end = pos
    while data[end] != 0:
        end += 1
    return bytes(data[pos:end]).decode("utf-8"), end + 1


def _properties(text: str) -> dict[str, str]:
    props = {}
    for entry in text.split(";"):
        entry = entry.strip()
        if entry:
            key, _, value = entry.partition("=")
            props[key] = value
    return props


def parse_container(data) -> tuple[ControlInfo, ControlInfo, int]:
    """
    Read the global and header blocks -> (global_info, header_info, dict_pos).

    The header's RDF payload (N-Triples metadata about the file) is skipped,
    not parsed: its byte length is in the header block's ``length`` property.
    Nothing in the query path needs it, and reading it would mean decoding
    text on every open.
    """
    global_info, pos = parse_control_info(data, 0)
    if global_info.control_type != GLOBAL:
        raise ValueError(f"expected a global control block, got type {global_info.control_type}")

    header_info, pos = parse_control_info(data, pos)
    if header_info.control_type != HEADER:
        raise ValueError(f"expected a header control block, got type {header_info.control_type}")

    try:
        length = int(header_info.properties["length"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"HDT header block has no usable 'length' property: {exc}") from exc

    return global_info, header_info, pos + length


def read_header_rdf(data, header_info: ControlInfo, pos: int) -> str:
    """The header's N-Triples metadata, read on demand (never at open)."""
    length = int(header_info.properties["length"])
    return bytes(data[pos:pos + length]).decode("utf-8")


__all__ = [
    "ControlInfo", "parse_control_info", "parse_container", "read_header_rdf",
    "COOKIE", "GLOBAL", "HEADER", "DICTIONARY", "TRIPLES", "vbyte_decode",
]
