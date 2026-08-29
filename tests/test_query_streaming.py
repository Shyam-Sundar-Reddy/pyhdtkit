"""Phase 6 acceptance: work scales with what the caller consumes, not with
the size of the data.

Peak memory is the plan's stated measure, but it is a noisy thing to assert on
at this corpus size -- the interpreter's own allocations dwarf 500 triples. The
underlying property is exact and testable: how many triples the store is asked
to produce, and how many files it opens, must track the LIMIT.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

import pyhdtkit.sparql
from pyhdtkit.sparql import HDTFile
from pyhdtkit.sparql.store.hdt_store import HDTStore

REPO = Path(__file__).resolve().parent / "fixtures" / "db"
GRAPH_SIZE = 505


def run(sparql):
    """Run a query, counting triples pulled from the store and files opened."""
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    reads = 0
    original = HDTStore.triples

    def counted(self, triple_pattern, context=None):
        nonlocal reads
        for item in original(self, triple_pattern, context):
            reads += 1
            yield item

    HDTStore.triples = counted
    try:
        rows = len(list(ds.query(sparql)))
    finally:
        HDTStore.triples = original
    opened = sum(1 for f in ds.store._files.get("urn:hdt:bian", {}).values() if f.is_open)
    return rows, reads, opened


SCAN = "SELECT * { GRAPH <urn:hdt:bian> { ?s ?p ?o } }"


@pytest.mark.parametrize("limit", [1, 5, 50])
def test_work_tracks_the_limit_not_the_graph(limit):
    rows, reads, _ = run(f"{SCAN} LIMIT {limit}")
    assert rows == limit
    assert reads == limit, f"pulled {reads} triples to return {limit}"
    assert reads < GRAPH_SIZE


def test_a_small_limit_leaves_most_files_unopened():
    _, _, opened = run(f"{SCAN} LIMIT 1")
    assert opened == 1, "a one-row query should open exactly one file"


def test_an_unlimited_query_does_read_everything():
    """The counterpart: without a LIMIT the work is the whole graph, which is
    what makes the LIMIT numbers above meaningful rather than accidental."""
    rows, reads, opened = run(SCAN)
    assert (rows, reads, opened) == (GRAPH_SIZE, GRAPH_SIZE, 9)


def test_reads_grow_linearly_with_the_limit():
    reads = [run(f"{SCAN} LIMIT {n}")[1] for n in (1, 10, 100)]
    assert reads == [1, 10, 100]


# -- nothing in the chain materialises ------------------------------------

def test_every_layer_yields_rather_than_builds():
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    store = ds.store
    assert isinstance(store.triples((None, None, None), None), types.GeneratorType)
    assert isinstance(store.contexts(), types.GeneratorType)

    handle = HDTFile(REPO / "hdt/sd/sd1/sd1_sample1.hdt")
    assert isinstance(handle.search(), types.GeneratorType)
    assert isinstance(handle.triples.search(), types.GeneratorType)
    handle.close()


def test_taking_one_triple_does_not_decode_the_file():
    """HDTFile.search must stop where the caller stops."""
    with HDTFile(REPO / "hdt/bian/bian1/bian1_sample1.hdt") as handle:
        total = len(handle)
        pulled = 0
        for _ in handle.search():
            pulled += 1
            break
        assert pulled == 1 < total


def test_len_never_opens_a_file():
    """Counting is served from the catalog, so it reads no HDT data at all."""
    ds = pyhdtkit.sparql.dataset(REPO / "map.json")
    assert len(ds.store) == 1218
    assert all(not f.is_open
               for readers in ds.store._files.values() for f in readers.values())
