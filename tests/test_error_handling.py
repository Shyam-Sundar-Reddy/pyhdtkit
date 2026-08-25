from pathlib import Path

import pytest

from pyhdtkit import hdt2ttl, hdtcat, ttl2hdt

FIXTURE = Path(__file__).parent / "fixtures" / "snikmeta.hdt"


def test_hdt2ttl_missing_input_raises_value_error(tmp_path) -> None:
    with pytest.raises(ValueError):
        hdt2ttl(tmp_path / "does_not_exist.hdt", tmp_path / "out.ttl")


def test_hdt2ttl_truncated_file_raises_value_error_not_index_error(tmp_path) -> None:
    # A 4-byte file used to raise a raw IndexError (indexing off the end of
    # the buffer) instead of the documented ValueError.
    truncated = tmp_path / "truncated.hdt"
    truncated.write_bytes(FIXTURE.read_bytes()[:4])
    with pytest.raises(ValueError):
        hdt2ttl(truncated, tmp_path / "out.ttl")


def test_hdt2ttl_garbage_file_raises_value_error(tmp_path) -> None:
    garbage = tmp_path / "garbage.hdt"
    garbage.write_bytes(b"not an hdt file at all, just garbage bytes")
    with pytest.raises(ValueError):
        hdt2ttl(garbage, tmp_path / "out.ttl")


def test_ttl2hdt_missing_input_raises_value_error(tmp_path) -> None:
    with pytest.raises(ValueError):
        ttl2hdt(tmp_path / "does_not_exist.ttl", tmp_path / "out.hdt")


def test_ttl2hdt_malformed_turtle_raises_value_error(tmp_path) -> None:
    bad = tmp_path / "bad.ttl"
    bad.write_text("this is @@@ not turtle")
    with pytest.raises(ValueError):
        ttl2hdt(bad, tmp_path / "out.hdt")


def test_hdtcat_missing_input_raises_value_error(tmp_path) -> None:
    with pytest.raises(ValueError):
        hdtcat([FIXTURE, tmp_path / "does_not_exist.hdt"], tmp_path / "out.hdt")
