import pytest

import pyhdtkit
from pyhdtkit import hdt2ttl, hdtcat, ttl2hdt


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


def test_hdt2ttl_not_implemented_yet(tmp_path) -> None:
    with pytest.raises(NotImplementedError):
        hdt2ttl(tmp_path / "in.hdt", tmp_path / "out.ttl")
