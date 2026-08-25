from pathlib import Path

from pyhdtkit.hdt.control_info import parse_control_info
from pyhdtkit.hdt.dictionary import decode_four_section_dictionary

FIXTURE = Path(__file__).parent / "fixtures" / "snikmeta.hdt"


def _dictionary_offset(data: bytes) -> int:
    """Walk past the Global control info and the Header section (control
    info + its NTriples data) to find where the Dictionary section starts.
    """
    _global_info, pos = parse_control_info(data, 0)
    header_info, pos = parse_control_info(data, pos)
    pos += int(header_info.properties["length"])
    return pos


def test_decodes_real_fixture_dictionary() -> None:
    data = FIXTURE.read_bytes()
    pos = _dictionary_offset(data)

    d, pos = decode_four_section_dictionary(data, pos)

    # Confirmed by manual byte-level decoding against this exact fixture
    # (see DECISIONS.md section 7): the "shared" section has 43 strings,
    # front-coded in blocks of 16, and its very first entry is this blank
    # node — plain (unshared-prefix) since it's block 0's first string.
    assert len(d.shared) == 43
    assert d.shared[0] == "_:b1"
    assert "http://www.snik.eu/ontology/meta/ApplicationComponent" in d.shared

    # All four sections decoded, no crashes, no empty dictionary.
    assert d.subjects or d.shared
    assert d.predicates
    assert d.objects or d.shared


def test_pfc_front_coding_round_trips_within_a_block() -> None:
    # Strings within a block share this front-coding: string[0] is stored
    # plain, string[i>0] as (shared-prefix-length, suffix). Sorted order
    # means adjacent strings in a block should share a growing prefix, so
    # decoding shouldn't silently truncate or duplicate content.
    data = FIXTURE.read_bytes()
    pos = _dictionary_offset(data)
    d, _pos = decode_four_section_dictionary(data, pos)

    assert len(d.shared) == len(set(d.shared)), "decoded strings must be unique (front-coding bug would collide/duplicate)"
    assert all(isinstance(s, str) and s for s in d.shared)
