"""BitmapTriples reader: decodes the Triples section into (subject_id,
predicate_id, object_id) tuples. ID -> string resolution is the caller's
job (via a FourSectionDictionary). See DECISIONS.md sections 6 and 7b.

On-disk layout, confirmed against ``snikmeta.hdt`` byte-for-byte (§7b was
open after Phase 0; resolved here the same way §5's array preamble was):
Triples Control Info, then BitmapY, BitmapZ, ArrayY, ArrayZ in that exact
order, ending at EOF.
"""

from __future__ import annotations

from pyhdtkit.hdt.binio import decode_bitmap, decode_log_array, encode_bitmap, encode_log_array
from pyhdtkit.hdt.control_info import build_control_info, parse_control_info

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


def encode_bitmap_triples(id_triples: list[Triple]) -> bytes:
    """Inverse of ``decode_bitmap_triples`` (SPO order only). ``id_triples``
    need not be pre-sorted or de-duplicated — both happen here, since every
    caller (ttl2hdt, hdtcat) wants that anyway and it's the one place that
    needs to agree with the adjacency-list encoding below.
    """
    sorted_triples = sorted(set(id_triples))

    bitmap_y: list[int] = []
    bitmap_z: list[int] = []
    array_y: list[int] = []
    array_z: list[int] = []

    n = len(sorted_triples)
    i = 0
    while i < n:
        subject = sorted_triples[i][0]
        subject_end = i
        while subject_end < n and sorted_triples[subject_end][0] == subject:
            subject_end += 1

        j = i
        while j < subject_end:
            predicate = sorted_triples[j][1]
            predicate_end = j
            while predicate_end < subject_end and sorted_triples[predicate_end][1] == predicate:
                predicate_end += 1

            array_y.append(predicate)
            bitmap_y.append(1 if predicate_end == subject_end else 0)
            for k in range(j, predicate_end):
                array_z.append(sorted_triples[k][2])
                bitmap_z.append(1 if k == predicate_end - 1 else 0)

            j = predicate_end
        i = subject_end

    control = build_control_info(4, "<http://purl.org/HDT/hdt#triplesBitmap>", "order=1;")
    return control + encode_bitmap(bitmap_y) + encode_bitmap(bitmap_z) + encode_log_array(array_y) + encode_log_array(array_z)
