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


def test_ttl2hdt_and_back_round_trips(tmp_path) -> None:
    ttl_in = tmp_path / "in.ttl"
    ttl_in.write_text(
        '@prefix ex: <http://example.org/> .\n'
        'ex:alice ex:knows ex:bob .\n'
        'ex:alice ex:name "Alice"@en .\n'
    )
    hdt_out = tmp_path / "out.hdt"
    ttl2hdt(ttl_in, hdt_out)
    assert hdt_out.exists() and hdt_out.stat().st_size > 0

    ttl_back = tmp_path / "back.ttl"
    hdt2ttl(hdt_out, ttl_back)
    text = ttl_back.read_text()
    assert "alice" in text.lower()
    assert "Alice" in text


def test_hdt2ttl_converts_real_fixture(tmp_path) -> None:
    out = tmp_path / "snikmeta.ttl"
    hdt2ttl(FIXTURE, out)
    ttl = out.read_text(encoding="utf-8")
    assert ttl
    assert "@prefix" in ttl or "<http" in ttl
    # Known content of this fixture (confirmed via manual decode, see
    # DECISIONS.md sec. 7): 328 triples, including this rdfs:label.
    assert "application component" in ttl
