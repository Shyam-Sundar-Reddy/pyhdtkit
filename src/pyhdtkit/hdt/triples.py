"""BitmapTriples reader: decodes the Triples section into (subject_id,
predicate_id, object_id) tuples. ID -> string resolution is the caller's
job (via a FourSectionDictionary). See DECISIONS.md sections 6 and 7b.

On-disk layout, confirmed against ``snikmeta.hdt`` byte-for-byte (§7b was
open after Phase 0; resolved here the same way §5's array preamble was):
Triples Control Info, then BitmapY, BitmapZ, ArrayY, ArrayZ in that exact
order, ending at EOF.
"""

from __future__ import annotations

from pyhdtkit.hdt.binio import decode_bitmap, decode_log_array
from pyhdtkit.hdt.control_info import parse_control_info

Triple = tuple[int, int, int]  # (subject_id, predicate_id, object_id), all 1-based


def decode_bitmap_triples(data: bytes, pos: int) -> tuple[list[Triple], int]:
    """Decode a Triples Control Info block plus its BitmapTriples data.

    Only ``order=SPO`` (HDT's default and by far the common case) is
    supported — anything else raises, rather than silently producing
    triples in the wrong subject/predicate/object slots.
    """
    info, pos = parse_control_info(data, pos)
    if "triplesBitmap" not in info.format:
        raise ValueError(f"unsupported triples format: {info.format!r} (only BitmapTriples is implemented)")
    order = info.properties.get("order")
    if order != "1":
        raise NotImplementedError(f"only SPO triple order (order=1) is implemented, got order={order!r}")

    bitmap_y, pos = decode_bitmap(data, pos)
    bitmap_z, pos = decode_bitmap(data, pos)
    array_y, pos = decode_log_array(data, pos)
    array_z, pos = decode_log_array(data, pos)

    # Adjacency-list decode: ArrayY holds one predicate ID per (S,P) pair,
    # BitmapY marks (with a 1) the last (S,P) pair for each subject. ArrayZ
    # holds one object ID per triple, BitmapZ marks the last object for
    # each (S,P) pair. Subjects/pairs are walked implicitly in order —
    # nothing in the file spells out subject IDs directly.
    triples: list[Triple] = []
    subject = 1
    z = 0
    for y, predicate in enumerate(array_y):
        while True:
            obj = array_z[z]
            triples.append((subject, predicate, obj))
            end_of_object_run = bitmap_z[z]
            z += 1
            if end_of_object_run:
                break
        if bitmap_y[y]:
            subject += 1

    return triples, pos
