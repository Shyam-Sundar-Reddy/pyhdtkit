"""
One ``.hdt`` file, opened lazily and searched by seeking.

This is the layer the query engine talks to. It maps rdflib terms to HDT
dictionary IDs, hands the ID pattern to :class:`BitmapTriples`, and resolves
the IDs of the triples that actually matched back into rdflib terms.

Nothing here decodes a whole file. The file is memory-mapped, so the operating
system pages in only the bytes the seeks touch, and a pattern naming a term the
file has never seen returns immediately without reading any triples at all.
"""

from __future__ import annotations

import mmap
from pathlib import Path
from typing import Iterator, Optional

from rdflib import BNode, Literal, URIRef
from rdflib.util import from_n3

from .dictionary import FourSectionDictionary
from .header import parse_container, read_header_rdf
from .triples import BitmapTriples

Term = URIRef | Literal | BNode
Triple = tuple[Term, Term, Term]

_UNBOUND = object()   # "no such term in this file", distinct from "unbound"


def decode_term(raw: str) -> Term:
    """
    One HDT term string -> a typed rdflib term.

    HDT writes IRIs bare -- no angle brackets -- so a term string is not valid
    N3 on its own. Literals (``"1"``, ``"x"@en``, ``"1"^^<...>``) and blank
    nodes (``_:b1``) are N3 and go to rdflib's parser; anything else is an IRI.
    Wrapping every term in URIRef instead, the obvious shortcut, yields a graph
    where ``?tier = "1"`` silently never matches.
    """
    return from_n3(raw) if raw[:1] in '"_' else URIRef(raw)


def encode_term(term: Term) -> str:
    """Inverse of :func:`decode_term`: an rdflib term -> its HDT string."""
    return str(term) if isinstance(term, URIRef) else term.n3()


class HDTFile:
    """
    A seekable reader over one HDT file.

    Usable as a context manager; otherwise the mapping is released when the
    object is garbage collected or :meth:`close` is called.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file = None
        self._data: Optional[mmap.mmap] = None
        self._dictionary: Optional[FourSectionDictionary] = None
        self._triples: Optional[BitmapTriples] = None
        self._header_pos = 0
        self._header_info = None
        self._terms: dict[tuple[int, int], Term] = {}   # (slot, id) -> term

    # -- opening -----------------------------------------------------------

    def _open(self) -> None:
        if self._data is not None:
            return
        self._file = open(self.path, "rb")
        try:
            self._data = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            _, self._header_info, pos = parse_container(self._data)
            self._header_pos = pos - int(self._header_info.properties["length"])
            self._dictionary = FourSectionDictionary(self._data, pos)
            self._triples = BitmapTriples(self._data, self._dictionary.end)
        except Exception:
            self.close()
            raise

    @property
    def dictionary(self) -> FourSectionDictionary:
        self._open()
        return self._dictionary

    @property
    def triples(self) -> BitmapTriples:
        self._open()
        return self._triples

    @property
    def header(self) -> str:
        """The file's own RDF metadata, read on demand."""
        self._open()
        return read_header_rdf(self._data, self._header_info, self._header_pos)

    @property
    def is_open(self) -> bool:
        return self._data is not None

    def close(self) -> None:
        if self._data is not None:
            self._data.close()
            self._data = None
        if self._file is not None:
            self._file.close()
            self._file = None
        self._dictionary = self._triples = None
        self._terms.clear()

    def __enter__(self) -> HDTFile:
        self._open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # -- term <-> id -------------------------------------------------------

    def _resolve(self, slot: int, id_: int) -> Term:
        """ID -> term, memoised. Repeated subjects and predicates are the norm,
        and each miss costs one block decode."""
        key = (slot, id_)
        term = self._terms.get(key)
        if term is None:
            dictionary = self._dictionary
            raw = (dictionary.subject_string, dictionary.predicate_string,
                   dictionary.object_string)[slot](id_)
            term = self._terms[key] = decode_term(raw)
        return term

    def _lookup(self, term: Optional[Term], slot: int):
        """Term -> ID, or None when unbound, or ``_UNBOUND`` when the file has
        never seen it (which makes the whole pattern unsatisfiable here)."""
        if term is None:
            return None
        dictionary = self._dictionary
        found = (dictionary.subject_id, dictionary.predicate_id,
                 dictionary.object_id)[slot](encode_term(term))
        return _UNBOUND if found is None else found

    # -- search ------------------------------------------------------------

    def search(
        self,
        subject: Optional[Term] = None,
        predicate: Optional[Term] = None,
        object_: Optional[Term] = None,
    ) -> Iterator[Triple]:
        """
        Yield triples matching the pattern; ``None`` is a wildcard.

        A bound subject seeks directly to that subject's triples. Any bound
        term absent from this file's dictionary short-circuits to no results
        without reading the triples section.
        """
        self._open()
        ids = [self._lookup(subject, 0), self._lookup(predicate, 1),
               self._lookup(object_, 2)]
        if _UNBOUND in ids:
            return

        for s, p, o in self._triples.search(*ids):
            yield self._resolve(0, s), self._resolve(1, p), self._resolve(2, o)

    def __iter__(self) -> Iterator[Triple]:
        return self.search()

    def __len__(self) -> int:
        """Triple count, read from the section header -- not by counting."""
        return self.triples.num_triples

    def predicates(self) -> list[Term]:
        """
        Every predicate in this file, from the dictionary alone.

        The predicate section is tiny next to subjects and objects, which is
        what makes predicate-based file pruning cheap.
        """
        self._open()
        return [decode_term(term) for term in self._dictionary.predicates]

    def predicate_counts(self) -> dict[str, int]:
        """Triples per predicate, keyed by the predicate's HDT string."""
        self._open()
        return {
            self._dictionary.predicate_string(pid): count
            for pid, count in self._triples.predicate_counts().items()
        }

    def term_counts(self) -> tuple[int, int]:
        """
        (distinct subjects, distinct objects), read from the dictionary
        section headers -- a count, not a scan.
        """
        self._open()
        shared = len(self._dictionary.shared)
        return shared + len(self._dictionary.subjects), shared + len(self._dictionary.objects)

    def verify(self) -> None:
        """Full CRC check of every section.

        Not done on open: it would mean reading the entire file, which is the
        one thing this reader exists to avoid. Header CRCs are always checked;
        this is the deliberate, explicit full pass.
        """
        self._open()
        self._dictionary.verify()
        self._triples.verify()

    def __repr__(self) -> str:
        state = "open" if self.is_open else "unopened"
        return f"<HDTFile {self.path.name} ({state})>"
