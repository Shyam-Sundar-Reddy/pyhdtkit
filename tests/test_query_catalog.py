"""Phase 3 acceptance: stats match a manual count, the cache survives a
restart, and rebuild() refreshes one URN without touching the others."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pyhdtkit.sparql import Catalog, CatalogBuilder, HDTFile, MappingLoader, encode_term
from pyhdtkit.sparql.catalog import CACHE_SUFFIX

REPO = Path(__file__).resolve().parent / "fixtures" / "db"
EXPECTED_TRIPLES = {"urn:hdt:bian": 505, "urn:hdt:kafka": 460, "urn:hdt:sd": 253}


@pytest.fixture
def workspace(tmp_path) -> Path:
    """A private copy of the mapping and its data, so tests can mutate files."""
    shutil.copytree(REPO / "hdt", tmp_path / "hdt")
    shutil.copy(REPO / "map.json", tmp_path / "map.json")
    return tmp_path


@pytest.fixture
def mapping(workspace):
    return MappingLoader(workspace / "map.json").load()


# -- stats are correct -----------------------------------------------------

def test_stats_match_a_manual_count(mapping):
    """The acceptance test: catalogue numbers must equal a real decode."""
    catalog = Catalog.load(mapping, save=False)
    for urn in mapping.urns():
        stats = catalog.stats_for(urn)
        manual_triples = 0
        manual_predicates: dict[str, int] = {}
        for path in mapping.files_for(urn):
            with HDTFile(path) as hdt:
                for _s, predicate, _o in hdt.search():
                    manual_triples += 1
                    key = encode_term(predicate)
                    manual_predicates[key] = manual_predicates.get(key, 0) + 1
        assert stats.triple_count == manual_triples == EXPECTED_TRIPLES[urn]
        assert stats.predicates == manual_predicates


def test_per_file_stats_cover_every_file(mapping):
    catalog = Catalog.load(mapping, save=False)
    for urn in mapping.urns():
        stats = catalog.stats_for(urn)
        assert len(stats.files) == 9
        assert stats.triple_count == sum(f.triple_count for f in stats.files)


def test_subject_and_object_counts_come_from_the_dictionary(mapping):
    """Distinct-term counts per file must match the file's own dictionary."""
    catalog = Catalog.load(mapping, save=False)
    stats = catalog.stats_for("urn:hdt:sd").files[0]
    with HDTFile(mapping.files_for("urn:hdt:sd")[0]) as hdt:
        subjects, objects = hdt.term_counts()
    assert (stats.subject_count, stats.object_count) == (subjects, objects)


# -- pruning, the reason it exists ----------------------------------------

def test_files_with_predicate_narrows_to_the_holders(mapping):
    catalog = Catalog.load(mapping, save=False)
    rare = "http://example.org/bian/deprecatedBy"
    assert catalog.stats_for("urn:hdt:bian").predicates[rare] == 1
    assert len(catalog.files_with_predicate("urn:hdt:bian", rare)) == 1


def test_predicate_in_no_file_prunes_everything(mapping):
    catalog = Catalog.load(mapping, save=False)
    assert catalog.files_with_predicate("urn:hdt:bian", "http://example.org/nope") == []


def test_unbound_predicate_prunes_nothing(mapping):
    catalog = Catalog.load(mapping, save=False)
    assert catalog.files_with_predicate("urn:hdt:bian", None) is None


# -- the cache -------------------------------------------------------------

def test_cache_is_written_and_survives_a_restart(mapping, workspace):
    catalog = Catalog.load(mapping)
    cache = workspace / f"map{CACHE_SUFFIX}"
    assert cache.is_file()

    # a second load must reuse the cache, not rebuild -- detectable because a
    # rebuild would stamp a new built_at
    again = Catalog.load(MappingLoader(workspace / "map.json").load())
    assert again.built_at == catalog.built_at
    assert again.triple_count() == catalog.triple_count()


def test_changed_file_invalidates_only_its_urn(mapping, workspace):
    Catalog.load(mapping)
    cache = json.loads((workspace / f"map{CACHE_SUFFIX}").read_text())
    before = {u: d["files"][0]["mtime"] for u, d in cache["urns"].items()}

    target = workspace / "hdt/sd/sd1/sd1_sample1.hdt"
    target.touch()                                   # same bytes, new mtime

    reloaded = Catalog.load(MappingLoader(workspace / "map.json").load())
    after = json.loads((workspace / f"map{CACHE_SUFFIX}").read_text())
    assert after["urns"]["urn:hdt:sd"]["files"][0]["mtime"] != before["urn:hdt:sd"]
    assert after["urns"]["urn:hdt:bian"]["files"][0]["mtime"] == before["urn:hdt:bian"]
    assert reloaded.triple_count("urn:hdt:sd") == EXPECTED_TRIPLES["urn:hdt:sd"]


def test_a_damaged_cache_is_rebuilt_not_fatal(mapping, workspace):
    (workspace / f"map{CACHE_SUFFIX}").write_text("{ not json at all")
    catalog = Catalog.load(MappingLoader(workspace / "map.json").load())
    assert catalog.triple_count() == sum(EXPECTED_TRIPLES.values())


def test_a_cache_from_an_older_version_is_discarded(mapping, workspace):
    (workspace / f"map{CACHE_SUFFIX}").write_text(json.dumps({"version": 0, "urns": {}}))
    catalog = Catalog.load(MappingLoader(workspace / "map.json").load())
    assert catalog.triple_count() == sum(EXPECTED_TRIPLES.values())


def test_cache_written_atomically_leaves_no_temp_file(mapping, workspace):
    Catalog.load(mapping).save()
    assert list(workspace.glob("*.tmp")) == []


# -- rebuild ---------------------------------------------------------------

def test_rebuild_one_urn_leaves_the_others_alone(mapping):
    catalog = Catalog.load(mapping, save=False)
    others = {u: catalog.stats_for(u) for u in ("urn:hdt:bian", "urn:hdt:kafka")}
    catalog.rebuild("urn:hdt:sd", save=False)
    for urn, stats in others.items():
        assert catalog.stats_for(urn) is stats          # same object: untouched
    assert catalog.triple_count("urn:hdt:sd") == EXPECTED_TRIPLES["urn:hdt:sd"]


def test_rebuild_all(mapping):
    catalog = Catalog.load(mapping, save=False)
    catalog.rebuild(save=False)
    assert catalog.triple_count() == sum(EXPECTED_TRIPLES.values())


def test_rebuild_unknown_urn_raises(mapping):
    catalog = Catalog.load(mapping, save=False)
    with pytest.raises(KeyError, match="urn:hdt:nope"):
        catalog.rebuild("urn:hdt:nope", save=False)


def test_stats_for_unknown_urn_raises(mapping):
    with pytest.raises(KeyError, match="urn:hdt:nope"):
        Catalog.load(mapping, save=False).stats_for("urn:hdt:nope")


# -- builder used directly -------------------------------------------------

def test_builder_builds_one_urn(mapping):
    stats = CatalogBuilder(mapping).build("urn:hdt:kafka")
    assert stats.triple_count == EXPECTED_TRIPLES["urn:hdt:kafka"]
    assert len(stats.files) == 9


def test_catalog_does_not_decode_triples(mapping, monkeypatch):
    """Building must stay on metadata: no triple search anywhere."""
    from pyhdtkit.sparql.hdt import reader

    def forbidden(*args, **kwargs):
        raise AssertionError("catalog build must not decode triples")

    monkeypatch.setattr(reader.HDTFile, "search", forbidden)
    assert CatalogBuilder(mapping).build("urn:hdt:sd").triple_count == 253
