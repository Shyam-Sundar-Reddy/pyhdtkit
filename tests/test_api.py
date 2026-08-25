from pathlib import Path

import pytest

import pyhdtkit
from pyhdtkit import hdt2ttl, hdtcat, ttl2hdt

FIXTURE = Path(__file__).parent / "fixtures" / "snikmeta.hdt"


def test_exports() -> None:
    assert pyhdtkit.__version__
    assert callable(ttl2hdt)
    assert callable(hdt2ttl)
    assert callable(hdtcat)


def test_hdtcat_requires_at_least_two_inputs() -> None:
    with pytest.raises(ValueError):
        hdtcat(["only-one.hdt"], "out.hdt")


def test_ttl2hdt_not_implemented_yet(tmp_path) -> None:
    with pytest.raises(NotImplementedError):
        ttl2hdt(tmp_path / "in.ttl", tmp_path / "out.hdt")


def test_hdt2ttl_converts_real_fixture(tmp_path) -> None:
    out = tmp_path / "snikmeta.ttl"
    hdt2ttl(FIXTURE, out)
    ttl = out.read_text(encoding="utf-8")
    assert ttl
    assert "@prefix" in ttl or "<http" in ttl
    # Known content of this fixture (confirmed via manual decode, see
    # DECISIONS.md sec. 7): 328 triples, including this rdfs:label.
    assert "application component" in ttl
