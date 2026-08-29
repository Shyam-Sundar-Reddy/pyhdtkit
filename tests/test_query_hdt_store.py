"""Phase 4 acceptance: SPARQL 1.1 over the HDT-backed Dataset."""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Dataset, Literal, URIRef

import pyhdtkit.sparql
from pyhdtkit.sparql import HDTStore, MappingLoader

REPO = Path(__file__).resolve().parent / "fixtures" / "db"
SIZES = {"urn:hdt:bian": 505, "urn:hdt:kafka": 460, "urn:hdt:sd": 253}
PREFIXES = """
PREFIX core: <http://example.org/core/>
PREFIX bian: <http://example.org/bian/>
PREFIX kf:   <http://example.org/kafka/>
PREFIX sd:   <http://example.org/sd/>
"""


@pytest.fixture(scope="module")
def ds() -> Dataset:
    return pyhdtkit.sparql.dataset(REPO / "map.json")


def q(ds, sparql):
    return list(ds.query(PREFIXES + sparql))


# -- graphs line up with the mapping ---------------------------------------

def test_one_context_per_urn(ds):
    named = {str(g.identifier) for g in ds.graphs()
             if str(g.identifier).startswith("urn:hdt:")}
    assert named == set(SIZES)


def test_graph_sizes_match_the_files(ds):
    for urn, size in SIZES.items():
        assert len(ds.graph(URIRef(urn))) == size


def test_store_len_across_all_graphs(ds):
    assert len(ds.store) == sum(SIZES.values())


# -- the plan's own acceptance query ---------------------------------------

def test_select_all_from_one_graph(ds):
    assert len(q(ds, "SELECT * { GRAPH <urn:hdt:sd> { ?s ?p ?o } }")) == SIZES["urn:hdt:sd"]


def test_graph_clause_isolates_graphs(ds):
    """A kafka type must not be findable in the sd graph."""
    assert q(ds, "SELECT ?s { GRAPH <urn:hdt:sd> { ?s a kf:Topic } }") == []
    assert len(q(ds, "SELECT ?s { GRAPH <urn:hdt:kafka> { ?s a kf:Topic } }")) == 63


def test_terms_keep_their_type_through_sparql(ds):
    rows = q(ds, "SELECT ?t { GRAPH <urn:hdt:sd> { core:svc_orders sd:tier ?t } }")
    assert rows == [(Literal("1"),)]


# -- cross-graph joins: the reason contexts exist --------------------------

def test_join_across_two_graphs(ds):
    rows = q(ds, """SELECT ?topic ?team {
        GRAPH <urn:hdt:kafka> { ?topic kf:producedBy ?svc }
        GRAPH <urn:hdt:sd>    { ?svc sd:owner ?team } }""")
    assert len(rows) == 63
    assert all(str(team).startswith("http://example.org/core/team_") for _, team in rows)


def test_join_across_three_graphs(ds):
    rows = q(ds, """SELECT (COUNT(*) AS ?n) {
        GRAPH <urn:hdt:bian>  { ?dom bian:supportsService ?svc }
        GRAPH <urn:hdt:kafka> { ?topic kf:producedBy ?svc }
        GRAPH <urn:hdt:sd>    { ?svc sd:owner ?team } }""")
    assert int(rows[0][0]) == 754


def test_graph_variable_binds_the_urn(ds):
    rows = q(ds, "SELECT DISTINCT ?g { GRAPH ?g { ?s a sd:Deployment } }")
    assert [str(g) for (g,) in rows] == ["urn:hdt:sd"]


# -- SPARQL 1.1 features rdflib supplies, driven through our Store ---------

def test_property_path(ds):
    rows = q(ds, "SELECT ?x { GRAPH <urn:hdt:sd> { core:svc_orders sd:dependsOn+ ?x } }")
    assert len(rows) == 6            # dependsOn forms a 6-cycle over the services


def test_aggregate_with_group_by(ds):
    rows = q(ds, """SELECT ?area (COUNT(*) AS ?n) {
        GRAPH <urn:hdt:bian> { ?d bian:businessArea ?area } }
        GROUP BY ?area ORDER BY ?area""")
    assert [int(n) for _, n in rows] == [24, 24, 24]


def test_optional_and_filter(ds):
    rows = q(ds, """SELECT ?t {
        GRAPH <urn:hdt:kafka> { ?t kf:partitions ?p . OPTIONAL { ?t kf:compaction ?c } }
        FILTER (?p = "12") }""")
    assert len(rows) == 12


def test_ask(ds):
    assert bool(ds.query(PREFIXES + "ASK { GRAPH <urn:hdt:bian> { ?s a bian:ServiceDomain } }"))
    assert not bool(ds.query(PREFIXES + "ASK { GRAPH <urn:hdt:bian> { ?s a kf:Topic } }"))


def test_default_union_sees_every_graph(ds):
    """No GRAPH clause: the union of all URNs, since dataset() sets default_union."""
    rows = q(ds, "SELECT (COUNT(*) AS ?n) { ?s ?p ?o }")
    assert int(rows[0][0]) == sum(SIZES.values())


# -- laziness: a single-graph query must not read the other graphs ---------

def test_query_on_one_graph_leaves_other_graphs_unread():
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    list(ds.query("SELECT * { GRAPH <urn:hdt:sd> { ?s ?p ?o } }"))
    opened = ds.store._files
    assert "urn:hdt:sd" in opened
    assert all(f.is_open for f in opened["urn:hdt:sd"].values())
    assert "urn:hdt:bian" not in opened and "urn:hdt:kafka" not in opened


# -- read-only ------------------------------------------------------------

@pytest.mark.parametrize("mutate", [
    lambda ds: ds.graph(URIRef("urn:hdt:sd")).add(
        (URIRef("urn:s"), URIRef("urn:p"), URIRef("urn:o"))),
    lambda ds: ds.graph(URIRef("urn:hdt:sd")).remove((None, None, None)),
    lambda ds: ds.store.addN([]),
])
def test_mutation_is_refused(ds, mutate):
    with pytest.raises(TypeError, match="read-only"):
        mutate(ds)


def test_store_can_be_built_from_a_mapping_directly():
    mapping = MappingLoader(REPO / "map.json").load()
    ds = Dataset(store=HDTStore(mapping), default_union=True)
    assert len(ds.graph(URIRef("urn:hdt:sd"))) == SIZES["urn:hdt:sd"]


# -- catalog-driven file pruning (Phase 3 wired into Phase 4) --------------

def opened_files(ds, urn):
    return sum(1 for f in ds.store._files.get(urn, {}).values() if f.is_open)


@pytest.mark.parametrize("sparql, rows, files", [
    ("SELECT * { GRAPH <urn:hdt:bian> { ?s bian:deprecatedBy ?o } }", 1, 1),
    ("SELECT * { GRAPH <urn:hdt:bian> { ?s bian:supportsService ?o } }", 72, 9),
    ("SELECT * { GRAPH <urn:hdt:bian> { ?s ?p ?o } }", 505, 9),
    ("SELECT * { GRAPH <urn:hdt:bian> { ?s bian:nosuchthing ?o } }", 0, 0),
])
def test_bound_predicate_opens_only_the_files_that_hold_it(sparql, rows, files):
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    assert len(q(ds, sparql)) == rows
    assert opened_files(ds, "urn:hdt:bian") == files


def test_len_is_served_from_the_catalog_without_opening_files():
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    assert len(ds.graph(URIRef("urn:hdt:sd"))) == SIZES["urn:hdt:sd"]
    assert opened_files(ds, "urn:hdt:sd") == 0


def test_close_releases_every_mapped_file():
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    q(ds, "SELECT * { GRAPH <urn:hdt:sd> { ?s ?p ?o } }")
    assert opened_files(ds, "urn:hdt:sd") == 9
    ds.store.close()
    assert ds.store._files == {}
