from rdflib import Graph

from pyhdtkit import hdt2ttl, hdtcat, ttl2hdt


def _triple_count(ttl_path) -> int:
    return len(Graph().parse(str(ttl_path), format="turtle"))


def test_hdtcat_merges_and_dedups(tmp_path) -> None:
    a_ttl = tmp_path / "a.ttl"
    a_ttl.write_text("@prefix ex: <http://example.org/> .\nex:a ex:knows ex:b .\nex:shared ex:p ex:o .\n")
    b_ttl = tmp_path / "b.ttl"
    b_ttl.write_text("@prefix ex: <http://example.org/> .\nex:c ex:knows ex:d .\nex:shared ex:p ex:o .\n")
    a_hdt, b_hdt = tmp_path / "a.hdt", tmp_path / "b.hdt"
    ttl2hdt(a_ttl, a_hdt)
    ttl2hdt(b_ttl, b_hdt)

    out = tmp_path / "combined.hdt"
    hdtcat([a_hdt, b_hdt], out)

    back = tmp_path / "combined.ttl"
    hdt2ttl(out, back)
    # 3 distinct triples expected: a-knows-b, c-knows-d, shared-p-o (deduped, not 4).
    assert _triple_count(back) == 3


def test_hdtcat_of_three_files(tmp_path) -> None:
    paths = []
    for i, (s, o) in enumerate([("a", "b"), ("b", "c"), ("c", "a")]):
        ttl = tmp_path / f"{i}.ttl"
        ttl.write_text(f"@prefix ex: <http://example.org/> .\nex:{s} ex:knows ex:{o} .\n")
        hdt = tmp_path / f"{i}.hdt"
        ttl2hdt(ttl, hdt)
        paths.append(hdt)

    out = tmp_path / "combined.hdt"
    hdtcat(paths, out)
    back = tmp_path / "combined.ttl"
    hdt2ttl(out, back)
    assert _triple_count(back) == 3
