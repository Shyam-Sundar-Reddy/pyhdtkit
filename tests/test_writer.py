from pathlib import Path

from pyhdtkit.hdt.reader import read_hdt, write_hdt

FIXTURE = Path(__file__).parent / "fixtures" / "snikmeta.hdt"


def test_real_fixture_survives_write_read_round_trip(tmp_path) -> None:
    original = read_hdt(FIXTURE)
    out = tmp_path / "rewritten.hdt"
    write_hdt(original, out)
    assert sorted(read_hdt(out)) == sorted(original)


def test_write_then_read_round_trips_exact_triples(tmp_path) -> None:
    triples = [
        ("http://example.org/alice", "http://example.org/knows", "http://example.org/bob"),
        ("http://example.org/alice", "http://example.org/knows", "http://example.org/carol"),
        ("http://example.org/bob", "http://example.org/knows", "http://example.org/alice"),
        ("http://example.org/alice", "http://example.org/name", '"Alice"@en'),
        ("http://example.org/alice", "http://example.org/age", '"30"^^<http://www.w3.org/2001/XMLSchema#integer>'),
        ("_:b1", "http://example.org/text", '"a blank node object"'),
        ("http://example.org/alice", "http://example.org/note", "_:b1"),
    ]

    out = tmp_path / "out.hdt"
    write_hdt(triples, out)
    round_tripped = read_hdt(out)

    assert sorted(round_tripped) == sorted(triples)


def test_write_dedups_repeated_triples(tmp_path) -> None:
    triples = [
        ("http://example.org/a", "http://example.org/p", "http://example.org/b"),
        ("http://example.org/a", "http://example.org/p", "http://example.org/b"),
    ]
    out = tmp_path / "out.hdt"
    write_hdt(triples, out)
    assert read_hdt(out) == [("http://example.org/a", "http://example.org/p", "http://example.org/b")]


def test_write_rejects_empty_triple_list(tmp_path) -> None:
    import pytest

    with pytest.raises(ValueError):
        write_hdt([], tmp_path / "out.hdt")


def test_write_reads_back_correctly_with_shared_terms(tmp_path) -> None:
    # "http://example.org/bob" appears as both a subject and an object,
    # so it belongs in the shared dictionary section — exercises the
    # shared/subjects-only/objects-only split.
    triples = [
        ("http://example.org/alice", "http://example.org/knows", "http://example.org/bob"),
        ("http://example.org/bob", "http://example.org/knows", "http://example.org/carol"),
    ]
    out = tmp_path / "out.hdt"
    write_hdt(triples, out)
    assert sorted(read_hdt(out)) == sorted(triples)
