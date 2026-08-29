"""
Reorder BGP triple patterns by real cardinality, using the catalog.

rdflib already reorders a BGP, but only by *shape*: ``reorderTriples`` sorts
by how many terms are bound. It has no way to know that one predicate matches
72 triples and another matches 1, so two patterns of the same shape tie and it
keeps the order they were written in. Evaluating the 72 first materialises 72
intermediate rows that the 1 then throws away.

This module breaks that tie with the catalog's per-predicate counts, then hands
the rewritten tree straight back to rdflib's evaluator. It never forks
rdflib's evaluation logic -- BGP reordering is semantically free (conjunction
commutes), so the results are identical and only the work changes.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from rdflib import BNode, URIRef, Variable
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.sparql import Query

from ..catalog import Catalog
from ..hdt import encode_term

Pattern = tuple


def is_bound(term) -> bool:
    """A term is bound when it is a concrete value, not a variable or bnode."""
    return not isinstance(term, (Variable, BNode))


def estimate(pattern: Pattern, catalog: Catalog, urns: Sequence[str]) -> float:
    """
    Estimated rows for one triple pattern across ``urns``.

    Starts from the catalog's count for the pattern's predicate -- or the
    graph's whole size when the predicate is unbound -- then divides by the
    distinct-term counts for each bound subject or object, the standard
    independence assumption.

    A bound predicate no file contains estimates 0, which sorts it first: the
    cheapest possible pattern is one that cannot match, and putting it first
    empties the BGP before anything else is read.
    """
    subject, predicate, object_ = pattern[0], pattern[1], pattern[2]
    total = 0.0
    for urn in urns:
        if urn not in catalog:
            continue
        stats = catalog.stats_for(urn)
        if is_bound(predicate) and isinstance(predicate, URIRef):
            rows = float(stats.predicates.get(encode_term(predicate), 0))
        else:
            rows = float(stats.triple_count)
        if rows:
            if is_bound(subject):
                rows /= max(stats.subject_count, 1)
            if is_bound(object_):
                rows /= max(stats.object_count, 1)
        total += rows
    return total


def _variables(pattern: Pattern) -> set:
    return {term for term in pattern[:3] if isinstance(term, (Variable, BNode))}


def reorder(patterns: Iterable[Pattern], catalog: Catalog,
            urns: Sequence[str]) -> list[Pattern]:
    """
    Order patterns cheapest-first, without breaking the join chain.

    A pure sort by cost can disconnect a join -- picking two patterns that
    share no variable forces a cartesian product between them. So this is
    greedy: cheapest pattern first, then repeatedly the cheapest pattern that
    shares a variable with what is already ordered, falling back to the
    cheapest remaining one when nothing connects.
    """
    remaining = list(patterns)
    if len(remaining) < 2:
        return remaining

    costs = {index: estimate(pattern, catalog, urns)
             for index, pattern in enumerate(remaining)}
    pending = list(range(len(remaining)))
    ordered: list[Pattern] = []
    bound: set = set()

    while pending:
        connected = [i for i in pending if _variables(remaining[i]) & bound]
        choose_from = connected if connected else pending
        best = min(choose_from, key=lambda i: (costs[i], i))
        pending.remove(best)
        ordered.append(remaining[best])
        bound |= _variables(remaining[best])

    return ordered


def optimize(node, catalog: Catalog, urns: Sequence[str]) -> None:
    """
    Rewrite every BGP in an algebra tree in place.

    Descends the whole tree, so BGPs inside OPTIONAL, UNION, MINUS and
    subqueries are reordered too. A ``GRAPH <urn>`` node narrows the statistics
    used beneath it to that one URN; everything else uses the union.
    """
    if isinstance(node, CompValue):
        if node.name == "Graph" and isinstance(node.get("term"), URIRef):
            urns = [str(node["term"])]
        if node.name == "BGP" and node.get("triples"):
            node["triples"] = reorder(node["triples"], catalog, urns)
        for value in list(node.values()):
            optimize(value, catalog, urns)
    elif isinstance(node, (list, tuple)):
        for item in node:
            optimize(item, catalog, urns)


def prepare(sparql: str, catalog: Catalog, urns: Optional[Sequence[str]] = None,
            init_ns: Optional[dict] = None) -> Query:
    """
    Parse and reorder ``sparql``, returning a Query rdflib can evaluate.

    The result goes straight to ``Dataset.query()``; rdflib's own evaluator
    runs it, reading the reordered tree.
    """
    query = translateQuery(parseQuery(sparql), initNs=init_ns or {})
    optimize(query.algebra, catalog, list(urns) if urns else catalog.urns())
    return query
