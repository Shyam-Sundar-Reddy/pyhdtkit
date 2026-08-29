"""Phase 2 acceptance: our own HDT decoder -- container, dictionary, seeks.

The correctness bar is cross-checked against pyhdtkit's independent bulk
decoder (a dev-only dependency, never imported by the engine): whatever it
reads out of a file, our seeking reader must read exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import BNode, Literal, URIRef

from pyhdtkit.sparql.hdt import Bitmap, HDTFile, LogArray, decode_term, encode_term
from pyhdtkit.sparql.hdt.header import COOKIE, parse_container

REPO = Path(__file__).resolve().parent / "fixtures" / "db"
SAMPLE = REPO / "hdt/sd/sd1/sd1_sample1.hdt"
ALL_FILES = sorted(REPO.glob("hdt/**/*.hdt"))
CORE = "http://example.org/core/"
SD = "http://example.org/sd/"

reference = pytest.importorskip("pyhdtkit.hdt.reader", reason="dev-only cross-check")


@pytest.fixture(scope="module")
def hdt():
    with HDTFile(SAMPLE) as handle:
        yield handle


def ref_triples(path):
    return sorted(reference.read_hdt(path))


def mine(handle):
    return sorted(tuple(encode_term(t) for t in triple) for triple in handle.search())


# -- container framing -----------------------------------------------------

def test_file_starts_with_the_hdt_cookie():
    assert SAMPLE.read_bytes()[:4] == COOKIE


def test_rejects_a_file_that_is_not_hdt(tmp_path):
    bogus = tmp_path / "bogus.hdt"
    bogus.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    with pytest.raises(ValueError, match="not an HDT section"):
        HDTFile(bogus).search().__next__()


def test_header_block_is_framed_and_skipped(hdt):
    """The container declares an ntriples header; these files carry an empty
    one (pyhdtkit writes length=0), so the reader must skip zero bytes and
    still land exactly on the dictionary."""
    hdt._open()
    assert hdt._header_info.format == "ntriples"
    assert hdt.header == ""
    assert len(hdt.dictionary.predicates) == 8      # landed on the dictionary


# -- dictionary ------------------------------------------------------------

def test_dictionary_section_sizes(hdt):
    d = hdt.dictionary
    assert (len(d.shared), len(d.subjects), len(d.predicates), len(d.objects)) == (9, 4, 8, 21)


def test_id_lookup_round_trips_every_term(hdt):
    """id_of and string_at must be inverses across all four sections."""
    d = hdt.dictionary
    for section in (d.shared, d.subjects, d.predicates, d.objects):
        for index, term in enumerate(section, start=1):
            assert section.id_of(term) == index
            assert section.string_at(index) == term


def test_shared_terms_take_low_ids_in_both_spaces(hdt):
    """A term used as subject and object resolves in both ID spaces."""
    d = hdt.dictionary
    term = CORE + "svc_orders"
    assert d.subject_id(term) == d.object_id(term) <= len(d.shared)
    assert d.subject_string(d.subject_id(term)) == term


def test_absent_term_returns_none_rather_than_raising(hdt):
    d = hdt.dictionary
    assert d.subject_id("http://example.org/nope") is None
    assert d.predicate_id("http://example.org/nope") is None
    assert d.object_id("http://example.org/nope") is None


def test_binary_search_is_exact_not_prefix(hdt):
    """A prefix of a real IRI is not in the dictionary."""
    assert hdt.dictionary.subject_id(CORE + "svc_order") is None
    assert hdt.dictionary.subject_id(CORE) is None


def test_string_at_rejects_out_of_range_ids(hdt):
    with pytest.raises(IndexError):
        hdt.dictionary.predicates.string_at(0)
    with pytest.raises(IndexError):
        hdt.dictionary.predicates.string_at(len(hdt.dictionary.predicates) + 1)


# -- bit-level primitives --------------------------------------------------

def test_bitmap_rank_and_select_agree_with_a_plain_scan(hdt):
    bitmap = hdt.triples.bitmap_y
    bits = [bitmap[i] for i in range(len(bitmap))]
    assert bitmap.count_ones == sum(bits)
    for i in range(len(bitmap) + 1):
        assert bitmap.rank1(i) == sum(bits[:i])
    ones = [i for i, b in enumerate(bits) if b]
    for k, position in enumerate(ones, start=1):
        assert bitmap.select1(k) == position
    assert bitmap.select1(0) == -1
    assert bitmap.select1(len(ones) + 1) == -1


def test_logarray_random_access_matches_sequential(hdt):
    array = hdt.triples.array_z
    assert [array[i] for i in range(len(array))] == list(array)
    with pytest.raises(IndexError):
        array[len(array)]


# -- triples: seeking ------------------------------------------------------

def test_triple_count_from_the_section_header(hdt):
    assert len(hdt) == hdt.triples.num_triples == 52


def test_subject_of_a_pair_matches_the_walk(hdt):
    triples = hdt.triples
    for y in range(len(triples.array_y)):
        start, stop = triples.pairs_of_subject(triples.subject_of(y))
        assert start <= y < stop


def test_seeking_a_subject_equals_filtering_a_scan(hdt):
    for subject in {s for s, _, _ in hdt.search()}:
        assert sorted(hdt.search(subject)) == sorted(t for t in hdt.search() if t[0] == subject)


def test_bound_predicate_and_object(hdt):
    for predicate in {p for _, p, _ in hdt.search()}:
        assert sorted(hdt.search(None, predicate)) == sorted(
            t for t in hdt.search() if t[1] == predicate)
    for object_ in {o for _, _, o in hdt.search()}:
        assert sorted(hdt.search(None, None, object_)) == sorted(
            t for t in hdt.search() if t[2] == object_)


def test_fully_bound_pattern(hdt):
    triple = next(iter(hdt.search()))
    assert list(hdt.search(*triple)) == [triple]


def test_absent_term_short_circuits_without_reading_triples(hdt):
    missing = URIRef("http://example.org/nope")
    assert list(hdt.search(missing)) == []
    assert list(hdt.search(None, missing)) == []
    assert list(hdt.search(None, None, missing)) == []


def test_search_streams(hdt):
    import types
    assert isinstance(hdt.search(), types.GeneratorType)


# -- terms -----------------------------------------------------------------

def test_terms_are_typed_by_kind_not_all_uriref(hdt):
    tier = list(hdt.search(URIRef(CORE + "svc_orders"), URIRef(SD + "tier")))
    assert tier == [(URIRef(CORE + "svc_orders"), URIRef(SD + "tier"), Literal("1"))]
    owner = list(hdt.search(URIRef(CORE + "svc_orders"), URIRef(SD + "owner")))
    assert owner[0][2] == URIRef(CORE + "team_platform")


@pytest.mark.parametrize("raw, term", [
    ("http://example.org/x", URIRef("http://example.org/x")),
    ('"1"', Literal("1")),
    ('"x"@en', Literal("x", lang="en")),
    ('"1"^^<http://www.w3.org/2001/XMLSchema#integer>',
     Literal("1", datatype=URIRef("http://www.w3.org/2001/XMLSchema#integer"))),
    ("_:b1", BNode("b1")),
])
def test_term_encoding_round_trips(raw, term):
    assert decode_term(raw) == term
    assert encode_term(term) == raw


# -- against the independent reference decoder -----------------------------

@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_matches_reference_decoder_exactly(path):
    with HDTFile(path) as handle:
        assert mine(handle) == ref_triples(path)


def test_every_file_passes_a_full_crc_check():
    for path in ALL_FILES:
        with HDTFile(path) as handle:
            handle.verify()


# -- resource handling -----------------------------------------------------

def test_file_is_not_opened_until_searched():
    handle = HDTFile(SAMPLE)
    assert not handle.is_open and "unopened" in repr(handle)
    next(handle.search(), None)
    assert handle.is_open
    handle.close()
    assert not handle.is_open


def test_missing_file_raises_on_use():
    with pytest.raises(OSError):
        next(HDTFile(REPO / "hdt/does-not-exist.hdt").search())
