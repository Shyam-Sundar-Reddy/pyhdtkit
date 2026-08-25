from pathlib import Path

from pyhdtkit.hdt.reader import read_hdt

FIXTURE = Path(__file__).parent / "fixtures" / "snikmeta.hdt"


def test_reads_correct_triple_count() -> None:
    triples = read_hdt(FIXTURE)
    # Confirmed via manual byte-level decode against this exact fixture
    # (DECISIONS.md sec. 7): BitmapZ has 328 set bits = 328 triples.
    assert len(triples) == 328


def test_all_triples_well_formed() -> None:
    triples = read_hdt(FIXTURE)
    for s, p, o in triples:
        assert isinstance(s, str) and s
        assert isinstance(p, str) and p.startswith("http")
        assert isinstance(o, str) and o
    assert len(triples) == len({(s, p, o) for s, p, o in triples}), "no duplicate triples expected"


def test_contains_known_triples() -> None:
    triples = read_hdt(FIXTURE)
    # Confirmed by manual decode (DECISIONS.md sec. 7): the first subject
    # decoded is a blank node restriction; ApplicationComponent is a known
    # owl:Class in this fixture with an English rdfs:label.
    assert (
        "http://www.snik.eu/ontology/meta/ApplicationComponent",
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
        "http://www.w3.org/2002/07/owl#Class",
    ) in triples
    assert (
        "http://www.snik.eu/ontology/meta/ApplicationComponent",
        "http://www.w3.org/2000/01/rdf-schema#label",
        '"application component"@en',
    ) in triples
