"""Top-level HDT file reader: Global control info -> Header -> Dictionary ->
Triples, assembled into HDT-dictionary-string triples (DECISIONS.md
section 8 — the same string shape ``pyhdtkit.ttl`` uses).
"""

from __future__ import annotations

from pathlib import Path

from pyhdtkit.hdt.control_info import parse_control_info
from pyhdtkit.hdt.dictionary import decode_four_section_dictionary
from pyhdtkit.hdt.triples import decode_bitmap_triples
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
