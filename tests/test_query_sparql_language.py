"""Phase 9: the SPARQL 1.1 surface -- paths, subqueries, aggregates,
BIND/VALUES, MINUS, EXISTS -- evaluated through the HDT store.

rdflib supplies the language; the point of these tests is that every construct
still gets correct data when the triples come from HDT rather than memory, and
that catalog reordering never changes an answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Literal, URIRef

import pyhdtkit.sparql

REPO = Path(__file__).resolve().parent / "fixtures" / "db"
P = """PREFIX core: <http://example.org/core/>
PREFIX bian: <http://example.org/bian/>
PREFIX kf:   <http://example.org/kafka/>
PREFIX sd:   <http://example.org/sd/>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
"""


@pytest.fixture(scope="module")
def ds():
    return pyhdtkit.sparql.dataset(REPO / "map.json")


def rows(ds, sparql):
    return list(ds.query(P + sparql))


def one(ds, sparql):
    """The single scalar a COUNT/SUM query returns."""
    return int(rows(ds, sparql)[0][0])


# -- property paths --------------------------------------------------------

def test_one_or_more_path_walks_the_dependency_cycle(ds):
    result = {str(x) for (x,) in rows(ds, """SELECT ?x {
        GRAPH <urn:hdt:sd> { core:svc_orders sd:dependsOn+ ?x } }""")}
    assert result == {f"http://example.org/core/svc_{name}" for name in
                      ("orders", "billing", "payments", "ledger", "notify", "audit")}


def test_fixed_length_sequence_path(ds):
    assert rows(ds, """SELECT ?x { GRAPH <urn:hdt:sd> {
        core:svc_orders sd:dependsOn/sd:dependsOn ?x } }""") == \
        [(URIRef("http://example.org/core/svc_payments"),)]


def test_alternative_path(ds):
    assert one(ds, """SELECT (COUNT(*) AS ?n) { GRAPH <urn:hdt:kafka> {
        ?t kf:producedBy|kf:consumedBy ?s } }""") == 126


def test_inverse_path(ds):
    assert one(ds, """SELECT (COUNT(*) AS ?n) { GRAPH <urn:hdt:sd> {
        core:team_platform ^sd:owner ?svc } }""") == 2


def test_zero_or_one_path(ds):
    assert one(ds, """SELECT (COUNT(DISTINCT ?x) AS ?n) { GRAPH <urn:hdt:sd> {
        core:svc_orders sd:dependsOn? ?x } }""") == 2


# -- aggregates ------------------------------------------------------------

def test_count_distinct(ds):
    assert one(ds, "SELECT (COUNT(DISTINCT ?t) AS ?n) { GRAPH <urn:hdt:kafka> { ?t a kf:Topic } }") == 63


def test_group_by_with_counts(ds):
    result = {str(a): int(n) for a, n in rows(ds, """SELECT ?area (COUNT(*) AS ?n) {
        GRAPH <urn:hdt:bian> { ?d bian:businessArea ?area } } GROUP BY ?area""")}
    assert result == {"Retail Banking": 24, "Corporate Banking": 24, "Risk Management": 24}


def test_having_filters_groups(ds):
    assert rows(ds, """SELECT ?area (COUNT(*) AS ?n) {
        GRAPH <urn:hdt:bian> { ?d bian:businessArea ?area } }
        GROUP BY ?area HAVING (COUNT(*) > 24)""") == []


def test_sum_avg_min_max_over_a_cast_literal(ds):
    row = rows(ds, """SELECT (SUM(?n) AS ?total) (MIN(?n) AS ?lo) (MAX(?n) AS ?hi) {
        GRAPH <urn:hdt:kafka> { ?t kf:partitions ?p } BIND(xsd:integer(?p) AS ?n) }""")[0]
    total, lo, hi = (int(v) for v in row)
    assert (lo, hi) == (3, 12) and total > 0


def test_group_concat(ds):
    value = str(rows(ds, """SELECT (GROUP_CONCAT(DISTINCT ?a; SEPARATOR="|") AS ?areas) {
        GRAPH <urn:hdt:bian> { ?d bian:businessArea ?a } }""")[0][0])
    assert sorted(value.split("|")) == ["Corporate Banking", "Retail Banking", "Risk Management"]


# -- subqueries ------------------------------------------------------------

def test_subquery_feeds_an_outer_join(ds):
    result = {str(t): int(n) for t, n in rows(ds, """SELECT ?team (SUM(?n) AS ?total) {
        { SELECT ?svc (COUNT(?t) AS ?n) {
            GRAPH <urn:hdt:kafka> { ?t kf:producedBy ?svc } } GROUP BY ?svc }
        GRAPH <urn:hdt:sd> { ?svc sd:owner ?team } } GROUP BY ?team""")}
    assert sum(result.values()) == 63          # every topic accounted for exactly once
    assert set(result) == {f"http://example.org/core/team_{t}"
                           for t in ("platform", "risk", "data")}


# -- BIND / VALUES ---------------------------------------------------------

def test_bind_computes_a_new_binding(ds):
    assert rows(ds, """SELECT ?upper { GRAPH <urn:hdt:sd> {
        core:svc_orders sd:label ?l } BIND(UCASE(?l) AS ?upper) }""") == \
        [(Literal("ORDERS SERVICE"),)]


def test_values_restricts_the_join(ds):
    result = {str(s) for (s,) in rows(ds, """SELECT ?svc {
        VALUES ?svc { core:svc_orders core:svc_audit core:svc_nope }
        GRAPH <urn:hdt:sd> { ?svc a sd:Service } }""")}
    assert result == {"http://example.org/core/svc_orders", "http://example.org/core/svc_audit"}


# -- MINUS / EXISTS / OPTIONAL / UNION ------------------------------------

def test_minus_removes_the_compacted_topic(ds):
    assert one(ds, """SELECT (COUNT(*) AS ?n) { GRAPH <urn:hdt:kafka> { ?t a kf:Topic }
        MINUS { GRAPH <urn:hdt:kafka> { ?t kf:compaction ?c } } }""") == 62


def test_filter_not_exists(ds):
    assert one(ds, """SELECT (COUNT(*) AS ?n) {
        GRAPH <urn:hdt:bian> { ?d a bian:ServiceDomain }
        FILTER NOT EXISTS { GRAPH <urn:hdt:bian> { ?d bian:deprecatedBy ?x } } }""") == 71


def test_filter_exists(ds):
    assert one(ds, """SELECT (COUNT(*) AS ?n) {
        GRAPH <urn:hdt:bian> { ?d a bian:ServiceDomain }
        FILTER EXISTS { GRAPH <urn:hdt:bian> { ?d bian:deprecatedBy ?x } } }""") == 1


def test_optional_leaves_the_variable_unbound(ds):
    result = rows(ds, """SELECT ?t ?c { GRAPH <urn:hdt:kafka> {
        ?t a kf:Topic OPTIONAL { ?t kf:compaction ?c } } }""")
    bound = [c for _, c in result if c is not None]
    assert len(result) == 63 and bound == [Literal("delete")]


def test_union_across_two_graphs(ds):
    assert one(ds, """SELECT (COUNT(DISTINCT ?s) AS ?n) {
        { GRAPH <urn:hdt:kafka> { ?s a kf:Broker } }
        UNION { GRAPH <urn:hdt:sd> { ?s a sd:Team } } }""") == 12


# -- FILTER ---------------------------------------------------------------

def test_filter_on_a_literal_value(ds):
    assert one(ds, """SELECT (COUNT(*) AS ?n) { GRAPH <urn:hdt:sd> { ?d sd:env ?e }
        FILTER (?e = "prod") }""") == 29


def test_filter_with_a_regex_and_a_function(ds):
    assert one(ds, """SELECT (COUNT(*) AS ?n) { GRAPH <urn:hdt:kafka> { ?t kf:name ?n }
        FILTER (REGEX(?n, "^orders\\\\.") && STRLEN(?n) > 10) }""") == 8


# -- solution modifiers ---------------------------------------------------

def test_order_limit_offset(ds):
    ordered = [str(s) for (s,) in rows(ds, """SELECT ?d { GRAPH <urn:hdt:bian> {
        ?d a bian:ServiceDomain } } ORDER BY ?d""")]
    window = [str(s) for (s,) in rows(ds, """SELECT ?d { GRAPH <urn:hdt:bian> {
        ?d a bian:ServiceDomain } } ORDER BY ?d LIMIT 3 OFFSET 5""")]
    assert window == ordered[5:8]


def test_distinct_collapses_duplicates(ds):
    assert one(ds, """SELECT (COUNT(*) AS ?n) { GRAPH <urn:hdt:bian> {
        ?d bian:businessArea ?a } }""") == 72
    assert len(rows(ds, """SELECT DISTINCT ?a { GRAPH <urn:hdt:bian> {
        ?d bian:businessArea ?a } }""")) == 3


# -- other query forms ----------------------------------------------------

def test_ask_true_and_false(ds):
    assert bool(ds.query(P + "ASK { GRAPH <urn:hdt:bian> { ?s a bian:ServiceDomain } }"))
    assert not bool(ds.query(P + "ASK { GRAPH <urn:hdt:bian> { ?s a kf:Topic } }"))


def test_construct_builds_a_graph(ds):
    graph = ds.query(P + """CONSTRUCT { ?s sd:owner ?t }
        WHERE { GRAPH <urn:hdt:sd> { ?s sd:owner ?t } }""").graph
    assert len(graph) == 6


def test_describe_returns_the_subject_triples(ds):
    assert len(ds.query(P + "DESCRIBE core:svc_orders").graph) == 5


# -- optimization must not perturb any of it ------------------------------

LANGUAGE_QUERIES = [
    "SELECT ?x { GRAPH <urn:hdt:sd> { core:svc_orders sd:dependsOn+ ?x } }",
    """SELECT (COUNT(*) AS ?n) { GRAPH <urn:hdt:kafka> { ?t a kf:Topic }
       MINUS { GRAPH <urn:hdt:kafka> { ?t kf:compaction ?c } } }""",
    """SELECT ?area (COUNT(*) AS ?n) { GRAPH <urn:hdt:bian> { ?d bian:businessArea ?area } }
       GROUP BY ?area ORDER BY ?area""",
    """SELECT ?topic ?team { GRAPH <urn:hdt:kafka> { ?topic kf:producedBy ?svc }
       GRAPH <urn:hdt:sd> { ?svc sd:owner ?team } }""",
]


@pytest.mark.parametrize("sparql", LANGUAGE_QUERIES, ids=range(len(LANGUAGE_QUERIES)))
def test_reordered_and_plain_agree(ds, sparql):
    assert sorted(map(str, ds.query(P + sparql))) == \
           sorted(map(str, pyhdtkit.sparql.query(ds, P + sparql)))
