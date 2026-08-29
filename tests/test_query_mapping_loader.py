"""Phase 1 acceptance: the real mapping loads; a corrupted one raises clearly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyhdtkit.sparql import Mapping, MappingError, MappingLoader

REPO = Path(__file__).resolve().parent / "fixtures" / "db"
REAL_MAP = REPO / "map.json"
EXPECTED = {"urn:hdt:bian": 9, "urn:hdt:kafka": 9, "urn:hdt:sd": 9}


@pytest.fixture(scope="module")
def mapping() -> Mapping:
    return MappingLoader(REAL_MAP).load()


def write_map(tmp_path: Path, doc, *, name="map.json") -> Path:
    path = tmp_path / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return path


# -- the real mapping ------------------------------------------------------

def test_real_mapping_loads_expected_urns(mapping):
    assert mapping.urns() == list(EXPECTED)
    assert len(mapping) == len(EXPECTED)


def test_files_for_returns_existing_hdt_files(mapping):
    for urn, count in EXPECTED.items():
        files = mapping.files_for(urn)
        assert len(files) == count
        assert all(f.is_file() and f.suffix == ".hdt" for f in files)


def test_contains(mapping):
    assert mapping.contains("urn:hdt:bian")
    assert not mapping.contains("urn:hdt:nope")


def test_files_for_unknown_urn_names_the_urn(mapping):
    with pytest.raises(MappingError, match="urn:hdt:nope"):
        mapping.files_for("urn:hdt:nope")


def test_returned_list_is_a_copy(mapping):
    mapping.files_for("urn:hdt:sd").clear()
    assert len(mapping.files_for("urn:hdt:sd")) == 9


def test_paths_resolve_relative_to_the_mapping_file(tmp_path, mapping):
    """A mapping moved elsewhere resolves against its own directory, not cwd."""
    moved = write_map(tmp_path, {"urn:a": ["data/one.hdt"]})
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "one.hdt").write_bytes(b"$HDT")
    assert MappingLoader(moved).load().files_for("urn:a") == [tmp_path / "data" / "one.hdt"]


# -- corrupted mappings must raise, naming the offender --------------------

def test_missing_file_raises_naming_urn_and_path(tmp_path):
    bad = write_map(tmp_path, {"urn:hdt:bian": ["hdt/gone.hdt"]})
    with pytest.raises(MappingError, match="missing file") as exc:
        MappingLoader(bad).load()
    assert "urn:hdt:bian" in str(exc.value) and "hdt/gone.hdt" in str(exc.value)


def test_wrong_extension_raises_before_touching_disk(tmp_path):
    src = tmp_path / "sample.ttl"
    src.write_text("")
    bad = write_map(tmp_path, {"urn:a": ["sample.ttl"]})
    with pytest.raises(MappingError, match=r"not an \.hdt file") as exc:
        MappingLoader(bad).load()
    assert "sample.ttl" in str(exc.value)


def test_duplicate_urn_raises_instead_of_keeping_the_last(tmp_path):
    bad = write_map(tmp_path, '{"urn:a": ["a.hdt"], "urn:a": ["b.hdt"]}')
    with pytest.raises(MappingError, match="duplicate URN"):
        MappingLoader(bad).load()


@pytest.mark.parametrize("value", ["hdt/one.hdt", 42, None, {"a": 1}])
def test_value_must_be_a_list_of_strings(tmp_path, value):
    bad = write_map(tmp_path, {"urn:a": value})
    with pytest.raises(MappingError, match="list of path strings"):
        MappingLoader(bad).load()


def test_empty_file_list_raises(tmp_path):
    bad = write_map(tmp_path, {"urn:a": []})
    with pytest.raises(MappingError, match="no files"):
        MappingLoader(bad).load()


def test_non_object_document_raises(tmp_path):
    bad = write_map(tmp_path, ["urn:a"])
    with pytest.raises(MappingError, match="must be a JSON object"):
        MappingLoader(bad).load()


def test_invalid_json_raises(tmp_path):
    bad = write_map(tmp_path, "{not json")
    with pytest.raises(MappingError, match="invalid JSON"):
        MappingLoader(bad).load()


def test_absent_mapping_file_raises(tmp_path):
    with pytest.raises(MappingError, match="cannot read mapping"):
        MappingLoader(tmp_path / "nope.json").load()


def test_one_bad_entry_fails_the_whole_load(tmp_path):
    """All-or-nothing: a good URN must not survive alongside a broken one."""
    good = REPO / "hdt/sd/sd1/sd1_sample1.hdt"
    bad = write_map(tmp_path, {"urn:ok": [str(good)], "urn:bad": ["gone.hdt"]})
    with pytest.raises(MappingError, match="missing file"):
        MappingLoader(bad).load()
