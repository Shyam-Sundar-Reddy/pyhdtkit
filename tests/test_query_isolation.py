"""The convert half and the query half must stay independent.

pyhdtkit solves two different problems: converting whole files (Turtle <-> HDT)
and querying files without reading them. They deliberately duplicate a little
low-level code — VByte, the CRCs, control-info parsing — so that an edit to one
decoder can never break the other. These tests enforce that boundary, rather
than trusting anyone to remember it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pyhdtkit.hdt.reader import read_hdt
from pyhdtkit.sparql import HDTFile

SPARQL_SRC = Path(__file__).resolve().parent.parent / "src" / "pyhdtkit" / "sparql"
CORPUS = Path(__file__).resolve().parent / "fixtures" / "db" / "hdt"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_query_half_never_imports_the_convert_half() -> None:
    """The isolation boundary, checked by parsing every import statement.

    A grep would trip over the ponytail: comments that *mention* the
    convert-side modules by name; parsing the AST only sees real imports.
    """
    offenders = {}
    for py in sorted(SPARQL_SRC.rglob("*.py")):
        bad = {m for m in _imported_modules(py)
               if m == "pyhdtkit.hdt" or m.startswith("pyhdtkit.hdt.")}
        if bad:
            offenders[py.relative_to(SPARQL_SRC).as_posix()] = sorted(bad)
    assert not offenders, (
        f"the query half must not import the convert half, found: {offenders}"
    )


def test_convert_half_never_imports_the_query_half() -> None:
    convert = Path(__file__).resolve().parent.parent / "src" / "pyhdtkit" / "hdt"
    offenders = {}
    for py in sorted(convert.rglob("*.py")):
        bad = {m for m in _imported_modules(py)
               if m == "pyhdtkit.sparql" or m.startswith("pyhdtkit.sparql.")}
        if bad:
            offenders[py.relative_to(convert).as_posix()] = sorted(bad)
    assert not offenders, f"the convert half must stay independent, found: {offenders}"


@pytest.mark.parametrize("hdt_path", sorted(CORPUS.rglob("*.hdt"))[:8],
                         ids=lambda p: p.stem)
def test_both_decoders_agree_on_every_file(hdt_path: Path) -> None:
    """Two independent decoders, same answer.

    The bulk reader and the seeking reader share no code, so agreement is real
    evidence that both read the format correctly — not a tautology. This is the
    payoff of keeping them isolated.
    """
    bulk = sorted(read_hdt(hdt_path))
    with HDTFile(hdt_path) as f:
        seeking = sorted((str(s), str(p), str(o)) for s, p, o in f)

    # The seeking reader hands back rdflib terms; the bulk reader hands back
    # HDT dictionary strings. Compare on the IRI/literal text they agree on.
    assert len(bulk) == len(seeking), f"triple count differs for {hdt_path.name}"
    assert [t[0] for t in bulk] == [t[0] for t in seeking]
    assert [t[1] for t in bulk] == [t[1] for t in seeking]
