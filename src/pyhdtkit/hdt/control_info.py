"""HDT "Control Information" block: the small header that precedes the
Header, Dictionary, and Triples sections (and one at the very start of the
file, for the whole container). See DECISIONS.md section 1.

Layout::

    "$HDT"      4 ASCII bytes, not null-terminated
    ControlType 1 byte (0=Unknown 1=Global 2=Header 3=Dictionary 4=Triples 5=Index)
    Format      null-terminated string
    Properties  "key=value;key=value;...;" null-terminated
    CRC16       2 bytes, little-endian, over every byte from "$" through the
                properties' null terminator (inclusive)
"""

from __future__ import annotations

from dataclasses import dataclass

from pyhdtkit.hdt.binio import crc16

COOKIE = b"$HDT"


@dataclass(frozen=True)
class ControlInfo:
    control_type: int
    format: str
    properties: dict[str, str]


def _parse_properties(text: str) -> dict[str, str]:
    props = {}
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        key, _, value = entry.partition("=")
        props[key] = value
    return props


def parse_control_info(data: bytes, pos: int) -> tuple[ControlInfo, int]:
    """Parse one Control Information block starting at ``pos``.

    Returns the parsed info and the position right after its CRC16.
    """
    start = pos
    if data[pos : pos + 4] != COOKIE:
        raise ValueError(f"expected HDT cookie '$HDT' at offset {pos}, found {data[pos:pos+4]!r}")
    pos += 4
    control_type = data[pos]
    pos += 1
    fmt_end = data.index(b"\x00", pos)
    fmt = data[pos:fmt_end].decode("utf-8")
    pos = fmt_end + 1
    props_end = data.index(b"\x00", pos)
    props_text = data[pos:props_end].decode("utf-8")
    pos = props_end + 1

    checked = data[start:pos]
    stored_crc = int.from_bytes(data[pos : pos + 2], "little")
    pos += 2
    if crc16(checked) != stored_crc:
        raise ValueError(f"control info CRC16 mismatch at offset {start}")

    return ControlInfo(control_type, fmt, _parse_properties(props_text)), pos
