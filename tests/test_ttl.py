from rdflib import Graph
from rdflib.compare import isomorphic

from pyhdtkit.ttl import parse_ttl, serialize_ttl

TTL = """\
@prefix ex: <http://example.org/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:alice ex:knows ex:bob .
ex:alice ex:name "Alice"@en .
ex:alice ex:age "30"^^xsd:integer .
ex:alice ex:note _:b1 .
_:b1 ex:text "a blank node object" .
"""


def test_parse_ttl_returns_correctly_typed_triples(tmp_path) -> None:
    ttl_path = tmp_path / "in.ttl"
    ttl_path.write_text(TTL)

    triples = parse_ttl(ttl_path)
    assert len(triples) == 5

    by_pred = {p: (s, o) for s, p, o in triples}
    assert by_pred["http://example.org/knows"] == (
        "http://example.org/alice",
        "http://example.org/bob",
    )
    assert by_pred["http://example.org/name"][1] == '"Alice"@en'
    assert by_pred["http://example.org/age"][1] == (
        '"30"^^<http://www.w3.org/2001/XMLSchema#integer>'
    )
    note_obj = by_pred["http://example.org/note"][1]
    assert note_obj.startswith("_:")


def test_parse_ttl_base_uri_resolves_relative_iris(tmp_path) -> None:
    ttl_path = tmp_path / "rel.ttl"
    ttl_path.write_text("<a> <b> <c> .\n")

    triples = parse_ttl(ttl_path, base_uri="http://example.org/")
    assert triples == [
        ("http://example.org/a", "http://example.org/b", "http://example.org/c")
    ]


def test_round_trip_through_serialize_and_reparse(tmp_path) -> None:
    # Blank-node label text isn't stable across independent parses (rdflib
    # mints fresh unique labels each time), so compare graphs by RDF
    # isomorphism rather than raw triple-string equality.
    ttl_in = tmp_path / "in.ttl"
    ttl_in.write_text(TTL)
    triples = parse_ttl(ttl_in)

    ttl_out = tmp_path / "out.ttl"
    serialize_ttl(triples, ttl_out)

    assert isomorphic(Graph().parse(ttl_in), Graph().parse(ttl_out))
