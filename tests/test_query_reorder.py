"""Phase 5 acceptance: the selective pattern is evaluated first, and that
measurably shrinks the intermediate results -- without changing any answer."""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Literal, URIRef, Variable
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import parseQuery

import pyhdtkit.sparql
from pyhdtkit.sparql.optimize import estimate, prepare, reorder
from pyhdtkit.sparql.store.hdt_store import HDTStore

REPO = Path(__file__).resolve().parent / "fixtures" / "db"
BIAN = "http://example.org/bian/"
KF = "http://example.org/kafka/"
SD = "http://example.org/sd/"
P = f"PREFIX bian: <{BIAN}> PREFIX kf: <{KF}> PREFIX sd: <{SD}> "

S, O, X = Variable("s"), Variable("o"), Variable("x")
RARE = (S, URIRef(BIAN + "deprecatedBy"), O)          # 1 triple in urn:hdt:bian
COMMON = (S, URIRef(BIAN + "supportsService"), O)     # 72 triples
ABSENT = (S, URIRef(BIAN + "nosuchthing"), O)         # 0 triples


@pytest.fixture(scope="module")
def ds():
    return pyhdtkit.sparql.dataset(REPO / "map.json")


@pytest.fixture(scope="module")
def catalog(ds):
    return ds.store.catalog


def bgp_of(query):
    return query.algebra["p"]["p"]["p"]["triples"]


def local(term):
    return str(term).rsplit("/", 1)[-1]


# -- the cost model --------------------------------------------------------

def test_estimate_reflects_real_predicate_counts(catalog):
    urns = ["urn:hdt:bian"]
    assert estimate(RARE, catalog, urns) == 1.0
    assert estimate(COMMON, catalog, urns) == 72.0
    assert estimate(RARE, catalog, urns) < estimate(COMMON, catalog, urns)


def test_absent_predicate_estimates_zero(catalog):
    """Cheapest possible pattern: it cannot match, so it should run first."""
    assert estimate(ABSENT, catalog, ["urn:hdt:bian"]) == 0.0


def test_unbound_predicate_estimates_the_whole_graph(catalog):
    assert estimate((S, Variable("p"), O), catalog, ["urn:hdt:bian"]) == 505.0


def test_bound_subject_lowers_the_estimate(catalog):
    bound = (URIRef(BIAN + "sd_1_1_0"), URIRef(BIAN + "supportsService"), O)
    assert estimate(bound, catalog, ["urn:hdt:bian"]) < estimate(COMMON, catalog, ["urn:hdt:bian"])


def test_estimate_sums_across_urns(catalog):
    pattern = (S, URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"), O)
    each = [estimate(pattern, catalog, [u]) for u in catalog.urns()]
    assert estimate(pattern, catalog, catalog.urns()) == sum(each)


def test_unknown_urn_contributes_nothing(catalog):
    assert estimate(COMMON, catalog, ["urn:hdt:nope"]) == 0.0


# -- ordering --------------------------------------------------------------

def test_selective_pattern_is_placed_first(catalog):
    assert reorder([COMMON, RARE], catalog, ["urn:hdt:bian"])[0] == RARE
    assert reorder([RARE, COMMON], catalog, ["urn:hdt:bian"])[0] == RARE


def test_absent_pattern_goes_ahead_of_everything(catalog):
    assert reorder([COMMON, RARE, ABSENT], catalog, ["urn:hdt:bian"])[0] == ABSENT


def test_single_pattern_is_untouched(catalog):
    assert reorder([COMMON], catalog, ["urn:hdt:bian"]) == [COMMON]
    assert reorder([], catalog, ["urn:hdt:bian"]) == []


def test_reordering_preserves_the_set_of_patterns(catalog):
    patterns = [COMMON, RARE, (S, URIRef(BIAN + "controlRecord"), X)]
    assert sorted(map(str, reorder(patterns, catalog, ["urn:hdt:bian"]))) == \
           sorted(map(str, patterns))


def test_join_chain_is_not_broken(catalog):
    """
    A pure cost sort could pick two patterns sharing no variable and force a
    cartesian product. Each pattern after the first must share a variable
    with something already ordered.
    """
    a = (S, URIRef(BIAN + "supportsService"), X)          # ?s -- ?x
    b = (X, URIRef(SD + "owner"), Variable("t"))          # ?x -- ?t
    c = (S, URIRef(BIAN + "deprecatedBy"), Variable("d"))  # ?s, cheapest
    ordered = reorder([a, b, c], catalog, catalog.urns())
    bound = {t for t in ordered[0][:3] if isinstance(t, Variable)}
    for pattern in ordered[1:]:
        variables = {t for t in pattern[:3] if isinstance(t, Variable)}
        assert variables & bound, f"{pattern} disconnected from {bound}"
        bound |= variables


# -- rewriting the algebra tree -------------------------------------------

def test_bgp_inside_a_graph_clause_is_reordered(catalog):
    query = prepare(P + """SELECT * { GRAPH <urn:hdt:bian> {
        ?s bian:supportsService ?svc . ?s bian:deprecatedBy ?d } }""", catalog)
    assert [local(t[1]) for t in bgp_of(query)] == ["deprecatedBy", "supportsService"]


def test_graph_clause_narrows_which_stats_are_used(catalog):
    """A predicate absent from the named graph must estimate 0 there, even
    though it is common in another graph."""
    kafka_type = (S, URIRef(KF + "producedBy"), O)
    assert estimate(kafka_type, catalog, ["urn:hdt:bian"]) == 0.0
    assert estimate(kafka_type, catalog, ["urn:hdt:kafka"]) == 63.0


def test_nested_patterns_are_reordered_too(catalog):
    """OPTIONAL, UNION and subqueries all carry BGPs of their own."""
    query = prepare(P + """SELECT * { GRAPH <urn:hdt:bian> { ?s a bian:ServiceDomain }
        OPTIONAL { GRAPH <urn:hdt:bian> {
            ?s bian:supportsService ?svc . ?s bian:deprecatedBy ?d } } }""", catalog)
    found = []

    def walk(node):
        from rdflib.plugins.sparql.parserutils import CompValue
        if isinstance(node, CompValue):
            if node.name == "BGP" and len(node.get("triples") or []) > 1:
                found.append([local(t[1]) for t in node["triples"]])
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(query.algebra)
    assert ["deprecatedBy", "supportsService"] in found


# -- the acceptance test: same answers, less work -------------------------

QUERIES = [
    P + """SELECT * { GRAPH <urn:hdt:bian> { ?s bian:supportsService ?svc .
           ?s bian:controlRecord ?cr . ?s bian:deprecatedBy ?d } }""",
    P + """SELECT ?topic ?team { GRAPH <urn:hdt:kafka> { ?topic kf:producedBy ?svc }
           GRAPH <urn:hdt:sd> { ?svc sd:owner ?team } }""",
    P + """SELECT (COUNT(*) AS ?n) { GRAPH <urn:hdt:kafka> { ?t a kf:Topic }
           OPTIONAL { GRAPH <urn:hdt:kafka> { ?t kf:compaction ?c } } }""",
    P + "SELECT ?x { GRAPH <urn:hdt:sd> { ?d sd:of ?x . ?d sd:env \"prod\" } }",
]


@pytest.mark.parametrize("sparql", QUERIES, ids=range(len(QUERIES)))
def test_reordering_never_changes_the_answer(ds, sparql):
    plain = sorted(map(str, ds.query(sparql)))
    tuned = sorted(map(str, pyhdtkit.sparql.query(ds, sparql)))
    assert plain == tuned


def count_store_reads(sparql, optimized):
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    reads = 0
    original = HDTStore.triples

    def counted(self, pattern, context=None):
        nonlocal reads
        for item in original(self, pattern, context):
            reads += 1
            yield item

    HDTStore.triples = counted
    try:
        rows = len(list(pyhdtkit.sparql.query(ds, sparql) if optimized else ds.query(sparql)))
    finally:
        HDTStore.triples = original
    return rows, reads


def test_intermediate_results_measurably_shrink():
    """
    The plan's acceptance test: deliberately mismatched selectivity, and the
    reordered plan must pull far fewer triples for the same answer.

    rdflib's own heuristic ties here -- all three patterns have one bound term
    and share ?s -- so it keeps written order and starts with a 72-row pattern.
    The catalog knows deprecatedBy matches once.
    """
    sparql = P + """SELECT * { GRAPH <urn:hdt:bian> {
        ?s bian:controlRecord ?cr . ?s bian:supportsService ?svc .
        ?s bian:deprecatedBy ?d } }"""
    plain_rows, plain_reads = count_store_reads(sparql, optimized=False)
    tuned_rows, tuned_reads = count_store_reads(sparql, optimized=True)

    assert plain_rows == tuned_rows == 1
    assert tuned_reads < plain_reads / 10, f"{tuned_reads} vs {plain_reads}"
