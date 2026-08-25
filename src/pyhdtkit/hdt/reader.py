"""Top-level HDT file reader/writer: Global control info -> Header ->
Dictionary -> Triples, assembled into (or built from) HDT-dictionary-string
triples (DECISIONS.md section 8 — the same string shape ``pyhdtkit.ttl``
uses).
"""

from __future__ import annotations

from pathlib import Path

from pyhdtkit.hdt.control_info import build_control_info, parse_control_info
from pyhdtkit.hdt.dictionary import (
    build_four_section_dictionary,
    decode_four_section_dictionary,
    encode_four_section_dictionary,
)
from pyhdtkit.hdt.triples import decode_bitmap_triples, encode_bitmap_triples
from pyhdtkit.ttl import Triple


def read_hdt(path: str | Path) -> list[Triple]:
    """Read an ``.hdt`` file into a list of HDT-dictionary-string triples."""
    data = Path(path).read_bytes()

    _global_info, pos = parse_control_info(data, 0)
    header_info, pos = parse_control_info(data, pos)
    pos += int(header_info.properties["length"])  # skip the header's NTriples text

    dictionary, pos = decode_four_section_dictionary(data, pos)
    id_triples, _pos = decode_bitmap_triples(data, pos)

    return [
        (
            dictionary.subject_string(s),
            dictionary.predicate_string(p),
            dictionary.object_string(o),
        )
        for s, p, o in id_triples
    ]


def write_hdt(triples: list[Triple], path: str | Path) -> None:
    """Write a list of HDT-dictionary-string triples out as an ``.hdt``
    file (Global control info, an empty Header, a FourSectionDictionary,
    and BitmapTriples in SPO order).
    """
    if not triples:
        raise ValueError("cannot write an HDT file with zero triples")

    ids = build_four_section_dictionary(triples)
    id_triples = [
        (ids.subject_id(s), ids.predicate_id(p), ids.object_id(o)) for s, p, o in triples
    ]

    global_info = build_control_info(1, "<http://purl.org/HDT/hdt#HDTv1>")
    header_data = b""  # minimal empty header — no VoID metadata, just a valid empty NTriples section
    header_info = build_control_info(2, "ntriples", f"length={len(header_data)};")
    dictionary_bytes = encode_four_section_dictionary(ids.dictionary)
    triples_bytes = encode_bitmap_triples(id_triples)

    Path(path).write_bytes(global_info + header_info + header_data + dictionary_bytes + triples_bytes)
