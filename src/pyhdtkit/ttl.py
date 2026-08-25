"""Turtle <-> internal triple-string conversion, via rdflib.

Internal representation (DECISIONS.md, section 8): a triple is a 3-tuple of
strings in HDT's own dictionary string format — bare IRI text (no angle
brackets), ``_:label`` for blank nodes, NTriples-style literal text (e.g.
``"42"^^<http://www.w3.org/2001/XMLSchema#integer>`` or ``"hello"@en``).
This is the one shared shape used on both the TTL side (here) and the HDT
dictionary encoder/decoder (Phase 2/3) — no separate Term class layer sits
between them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.term import Node

Triple = tuple[str, str, str]

# Matches a full NTriples-style literal string: "value"[^^<datatype> | @lang]
_LITERAL_RE = re.compile(
    r'^"(?P<value>(?:[^"\\]|\\.)*)"'
    r"(?:\^\^<(?P<datatype>[^>]*)>|@(?P<lang>[A-Za-z]+(?:-[A-Za-z0-9]+)*))?$"
)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), s)


def term_to_string(term: Node) -> str:
    """Convert an rdflib term to its HDT dictionary string form."""
    if isinstance(term, URIRef):
        return str(term)
    if isinstance(term, BNode):
        return f"_:{term}"
    if isinstance(term, Literal):
        return term.n3()
    raise ValueError(f"unsupported RDF term type: {type(term)!r}")


def string_to_term(s: str) -> Node:
    """Convert an HDT dictionary string back to an rdflib term."""
    if s.startswith("_:"):
        return BNode(s[2:])
    if s.startswith('"'):
        m = _LITERAL_RE.match(s)
        if not m:
            raise ValueError(f"malformed literal string: {s!r}")
        value = _unescape(m.group("value"))
        if m.group("datatype"):
            return Literal(value, datatype=URIRef(m.group("datatype")))
        if m.group("lang"):
            return Literal(value, lang=m.group("lang"))
        return Literal(value)
    return URIRef(s)


def parse_ttl(path: str | Path, *, base_uri: str | None = None) -> list[Triple]:
    """Parse a Turtle file into a list of HDT-format triple-strings."""
    graph = Graph()
    graph.parse(str(path), format="turtle", publicID=base_uri)
    return [(term_to_string(s), term_to_string(p), term_to_string(o)) for s, p, o in graph]


def serialize_ttl(triples: Iterable[Triple], path: str | Path) -> None:
    """Write HDT-format triple-strings out as a Turtle file."""
    graph = Graph()
    for s, p, o in triples:
        graph.add((string_to_term(s), string_to_term(p), string_to_term(o)))
    graph.serialize(destination=str(path), format="turtle")
