"""
An rdflib ``Store`` backed by the URN mapping, the HDT reader, and the catalog.

This is the whole engine. rdflib owns the SPARQL language -- parsing, joins,
property paths, aggregates, subqueries, OPTIONAL/UNION/MINUS -- and drives it
all through ``triples()`` and ``contexts()`` below. There is deliberately no
join logic here: this layer only answers "given a triple pattern and a URN,
which triples match", and it answers it by reading as little as possible:

1. the catalog names which files can hold the pattern's predicate, so the rest
   are never opened;
2. a bound term absent from a file's dictionary ends that file immediately;
3. a bound subject seeks to its triples instead of scanning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from rdflib import Dataset, Graph, URIRef
from rdflib.store import Store

from ..catalog import Catalog
from ..hdt import HDTFile, encode_term
from ..mapping import Mapping, MappingLoader

READ_ONLY = "HDTStore is read-only: HDT files are immutable (see docs/data_model.md)"


class HDTStore(Store):
    """
    Read-only, context-aware Store over a :class:`~pyhdtkit.sparql.mapping.Mapping`.

        store = HDTStore(MappingLoader("map.json").load())
        ds = Dataset(store=store, default_union=True)
        ds.query("SELECT * { GRAPH <urn:hdt:sd> { ?s ?p ?o } }")

    One URN is one context. Files are opened on first touch, so a query naming
    a single GRAPH never reads the other graphs, and a query naming a rare
    predicate never reads the files that lack it.
    """

    context_aware = True
    graph_aware = True
    formula_aware = False
    transaction_aware = False

    def __init__(self, mapping: Mapping, catalog: Optional[Catalog] = None,
                 configuration=None, identifier=None) -> None:
        super().__init__(configuration, identifier)
        self.mapping = mapping
        self.catalog = catalog if catalog is not None else Catalog.load(mapping)
        self._base = mapping.source.parent
        self._files: dict[str, dict[str, HDTFile]] = {}
        self._graphs: dict[str, Graph] = {}
        self._namespaces: dict[str, URIRef] = {}

    # -- file resolution ---------------------------------------------------

    def _key(self, path: Path) -> str:
        """Catalog-relative path, the key both layers agree on."""
        return path.relative_to(self._base).as_posix() if path.is_relative_to(self._base) \
            else path.as_posix()

    def _readers(self, urn: str) -> dict[str, HDTFile]:
        """Readers for a URN's files. Constructing one opens nothing."""
        if urn not in self._files:
            self._files[urn] = {self._key(p): HDTFile(p) for p in self.mapping.files_for(urn)}
        return self._files[urn]

    def _candidates(self, urn: str, predicate) -> Iterator[HDTFile]:
        """
        The files worth searching for a pattern with this predicate.

        With a bound predicate the catalog answers which files contain it, and
        everything else is skipped unopened. This is the coarse-grained
        counterpart to the dictionary short-circuit inside each file: the
        catalog avoids the open, the dictionary avoids the read.
        """
        readers = self._readers(urn)
        if predicate is None:
            return iter(readers.values())
        allowed = self.catalog.files_with_predicate(urn, encode_term(predicate))
        return (readers[path] for path in allowed if path in readers)

    def _urns_for(self, context) -> list[str]:
        """Which graphs a call applies to: the one named, else all of them."""
        if context is None:
            return self.mapping.urns()
        urn = str(getattr(context, "identifier", context))
        return [urn] if self.mapping.contains(urn) else []

    # -- reading -----------------------------------------------------------

    def _graph(self, urn: str) -> Graph:
        """The Graph handle for a URN, made once and reused."""
        graph = self._graphs.get(urn)
        if graph is None:
            graph = self._graphs[urn] = Graph(store=self, identifier=URIRef(urn))
        return graph

    def triples(self, triple_pattern, context=None) -> Iterator:
        """
        Yield ``((s, p, o), contexts)`` for every match, as rdflib expects.

        A pattern position is ``None`` for a wildcard. Results stream: a
        LIMITed query stops pulling and the remaining files are never read.
        Triples repeated across a URN's files are yielded once per graph.

        Deduplication is skipped when only one file can match, which is the
        common case once the catalog has pruned. That matters for more than
        speed: the ``seen`` set grows with the result, so keeping it for a
        single-file read would quietly undo the streaming guarantee on a large
        graph.
        """
        subject, predicate, object_ = triple_pattern
        for urn in self._urns_for(context):
            graph = self._graph(urn)
            candidates = list(self._candidates(urn, predicate))
            if len(candidates) == 1:
                for triple in candidates[0].search(subject, predicate, object_):
                    yield triple, iter((graph,))
                continue
            seen: set = set()
            for hdt in candidates:
                for triple in hdt.search(subject, predicate, object_):
                    if triple not in seen:
                        seen.add(triple)
                        yield triple, iter((graph,))

    def contexts(self, triple=None) -> Iterator[Graph]:
        """
        Yield one Graph per URN -- this is what makes GRAPH and FROM NAMED work.

        With a triple, yield only the graphs containing it.
        """
        for urn in self.mapping.urns():
            graph = self._graph(urn)
            if triple is None or any(
                next(hdt.search(*triple), None) is not None
                for hdt in self._candidates(urn, triple[1])
            ):
                yield graph

    def __len__(self, context=None) -> int:
        """
        Triple count, served from the catalog rather than by scanning.

        Counts a triple once per file it appears in, so for a URN whose files
        overlap this is an upper bound on distinct triples. Deduplicating
        would mean reading every triple, which is what the catalog exists to
        avoid.
        """
        return sum(self.catalog.triple_count(urn) for urn in self._urns_for(context))

    # -- namespaces (rdflib binds prefixes through the store) --------------

    def bind(self, prefix, namespace, override=True) -> None:
        self._namespaces[prefix] = namespace

    def prefix(self, namespace) -> Optional[str]:
        return next((p for p, n in self._namespaces.items() if n == namespace), None)

    def namespace(self, prefix) -> Optional[URIRef]:
        return self._namespaces.get(prefix)

    def namespaces(self) -> Iterator[tuple[str, URIRef]]:
        return iter(self._namespaces.items())

    # -- lifecycle ---------------------------------------------------------

    def close(self, commit_pending_transaction=False) -> None:
        """Release every memory-mapped file."""
        for readers in self._files.values():
            for hdt in readers.values():
                hdt.close()
        self._files.clear()
        self._graphs.clear()

    # -- writing: refused --------------------------------------------------

    def add(self, triple, context, quoted=False):
        raise TypeError(READ_ONLY)

    def addN(self, quads):
        raise TypeError(READ_ONLY)

    def remove(self, triple, context=None):
        raise TypeError(READ_ONLY)

    def add_graph(self, graph) -> None:
        # rdflib.Dataset creates its default graph on construction; accepting
        # that silently is what lets a read-only store be used as a Dataset.
        pass

    def remove_graph(self, graph):
        raise TypeError(READ_ONLY)


def query(ds: Dataset, sparql: str, init_ns: Optional[dict] = None):
    """
    Run SPARQL with catalog-driven pattern reordering.

    ``ds.query(sparql)`` works too and gives identical answers -- this routes
    the query through Phase 5 first, so the selective patterns are evaluated
    before the broad ones.
    """
    from ..optimize import prepare

    return ds.query(prepare(sparql, ds.store.catalog, init_ns=init_ns))


def dataset(map_path: str | Path = "map.json", default_union: bool = True,
            catalog: Optional[Catalog] = None) -> Dataset:
    """
    The one-call entry point: mapping file in, queryable Dataset out.

    ``default_union=True`` makes a query without a GRAPH clause see every URN,
    which is almost always what you want when the mapping is the whole world.
    """
    mapping = MappingLoader(map_path).load()
    return Dataset(
        store=HDTStore(mapping, catalog=catalog), default_union=default_union
    )
