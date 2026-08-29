"""Phase 9: end to end at progressive scale, against the real folder tree.

    1 HDT -> 1 URN, multi-file -> nested folders -> cross-graph -> full dataset

Every assertion here is on exact expected bindings, cross-checked against the
Turtle sources rather than against whatever the engine happens to return.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from rdflib import Literal, URIRef

import pyhdtkit.sparql

REPO = Path(__file__).resolve().parent / "fixtures" / "db"
TTL = REPO / "ttl"
P = """PREFIX core: <http://example.org/core/>
PREFIX bian: <http://example.org/bian/>
PREFIX kf:   <http://example.org/kafka/>
PREFIX sd:   <http://example.org/sd/>
"""


def dataset_over(tmp_path: Path, entries: dict[str, list[str]]):
    """A dataset over a private mapping naming only the given HDT files."""
    shutil.copytree(REPO / "hdt", tmp_path / "hdt", dirs_exist_ok=True)
    (tmp_path / "map.json").write_text(json.dumps(entries), encoding="utf-8")
    return pyhdtkit.sparql.dataset(tmp_path / "map.json")


def ttl_count(pattern: str, *globs: str) -> int:
    """Ground truth straight from the Turtle sources."""
    return sum(len(re.findall(pattern, path.read_text(encoding="utf-8"), re.MULTILINE))
               for glob in globs for path in sorted(TTL.glob(glob)))


# -- step 1: one HDT file, one URN ----------------------------------------

def test_single_file_urn(tmp_path):
    ds = dataset_over(tmp_path, {"urn:hdt:one": ["hdt/sd/sd1/sd1_sample1.hdt"]})
    assert len(list(ds.query("SELECT * { GRAPH <urn:hdt:one> { ?s ?p ?o } }"))) == 52

    owner = list(ds.query(P + """SELECT ?t { GRAPH <urn:hdt:one> {
        core:svc_orders sd:owner ?t } }"""))
    assert owner == [(URIRef("http://example.org/core/team_platform"),)]


# -- step 2: several files in one URN --------------------------------------

def test_multi_file_urn_unions_its_files(tmp_path):
    files = [f"hdt/sd/sd1/sd1_sample{i}.hdt" for i in (1, 2, 3)]
    ds = dataset_over(tmp_path, {"urn:hdt:sd1": files})
    expected = ttl_count(r"^\S+ a ", "sd/sd1/*.ttl")     # subjects declared in sd1
    assert len(list(ds.query(P + "SELECT ?s { GRAPH <urn:hdt:sd1> { ?s a ?type } }"))) == expected


# -- step 3: nested folders collapse into one graph ------------------------

def test_nested_folders_are_one_flat_graph(tmp_path):
    """Subfolders carry no semantics: sd1/sd2/sd3 are one URN, not three."""
    files = [f"hdt/sd/sd{g}/sd{g}_sample{s}.hdt" for g in (1, 2, 3) for s in (1, 2, 3)]
    ds = dataset_over(tmp_path, {"urn:hdt:sd": files})
    assert len(ds.graph(URIRef("urn:hdt:sd"))) == 253

    deployments = ttl_count(r"a sd:Deployment", "sd/*/*.ttl")
    assert len(list(ds.query(P + """SELECT ?d { GRAPH <urn:hdt:sd> {
        ?d a sd:Deployment } }"""))) == deployments


# -- step 4: cross-graph GRAPH query ---------------------------------------

def test_cross_graph_join_over_the_full_mapping():
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    rows = list(ds.query(P + """SELECT ?topic ?team {
        GRAPH <urn:hdt:kafka> { ?topic kf:producedBy ?svc }
        GRAPH <urn:hdt:sd>    { ?svc sd:owner ?team } }"""))
    assert len(rows) == ttl_count(r"kf:producedBy", "kafka/*/*.ttl")
    assert all(str(team).startswith("http://example.org/core/team_") for _, team in rows)


def test_three_graph_join_exact_bindings():
    """A fully-specified path bian -> shared service -> kafka -> sd."""
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    rows = list(ds.query(P + """SELECT ?team {
        GRAPH <urn:hdt:bian>  { bian:sd_1_1_0 bian:supportsService ?svc }
        GRAPH <urn:hdt:kafka> { ?topic kf:producedBy ?svc }
        GRAPH <urn:hdt:sd>    { ?svc sd:owner ?team } }"""))
    assert {str(t) for (t,) in rows} == {"http://example.org/core/team_data"}


# -- step 5: the full dataset ---------------------------------------------

@pytest.fixture(scope="module")
def full():
    return pyhdtkit.sparql.dataset(REPO / "map.json")


def test_full_dataset_sizes(full):
    assert {str(g.identifier): len(g) for g in full.graphs()
            if str(g.identifier).startswith("urn:hdt:")} == \
        {"urn:hdt:bian": 505, "urn:hdt:kafka": 460, "urn:hdt:sd": 253}


def test_default_union_covers_every_urn(full):
    """No GRAPH clause: every URN, and the total must equal the Turtle sources.

    Each predicate-object pair in the fixtures ends in ';' or '.', one per
    triple, so counting those line endings is an independent triple count.
    """
    total = int(list(full.query("SELECT (COUNT(*) AS ?n) { ?s ?p ?o }"))[0][0])
    assert total == 1218
    assert total == ttl_count(r"^(?!@prefix).*[;.]\s*$", "*/*/*.ttl")


def test_representative_property_path(full):
    """Required by the plan: at least one property path on real data."""
    assert {str(x) for (x,) in full.query(P + """SELECT ?x { GRAPH <urn:hdt:sd> {
        core:svc_orders sd:dependsOn+ ?x } }""")} == \
        {f"http://example.org/core/svc_{n}" for n in
         ("orders", "billing", "payments", "ledger", "notify", "audit")}


def test_representative_aggregate(full):
    """Required by the plan: at least one aggregate on real data."""
    rows = {str(a): int(n) for a, n in full.query(P + """SELECT ?area (COUNT(*) AS ?n) {
        GRAPH <urn:hdt:bian> { ?d bian:businessArea ?area } } GROUP BY ?area""")}
    assert rows == {area: ttl_count(rf'bian:businessArea "{area}"', "bian/*/*.ttl")
                    for area in rows}
    assert sum(rows.values()) == 72


def test_rare_predicate_exact_binding(full):
    """The single deprecatedBy triple in the whole corpus."""
    assert list(full.query(P + "SELECT ?s ?d { GRAPH <urn:hdt:bian> { ?s bian:deprecatedBy ?d } }")) == \
        [(URIRef("http://example.org/bian/sd_1_1_0"), URIRef("http://example.org/bian/sd_1_1_1"))]


def test_literal_bindings_keep_their_lexical_form(full):
    assert list(full.query(P + """SELECT ?tier ?label { GRAPH <urn:hdt:sd> {
        core:svc_billing sd:tier ?tier ; sd:label ?label } }""")) == \
        [(Literal("2"), Literal("Billing Service"))]


# -- a broken mapping fails the whole query, loudly -----------------------

def test_a_missing_file_stops_everything(tmp_path):
    (tmp_path / "map.json").write_text(json.dumps({"urn:hdt:x": ["hdt/gone.hdt"]}))
    with pytest.raises(pyhdtkit.sparql.MappingError, match="missing file"):
        pyhdtkit.sparql.dataset(tmp_path / "map.json")
