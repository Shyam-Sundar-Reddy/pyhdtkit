"""Read-only SPARQL 1.1 over URN-addressed HDT files.

    from pyhdtkit import dataset, build_catalog

    ds = dataset("mydb/")
    ds.query("SELECT * { GRAPH <urn:hdt:sd> { ?s ?p ?o } }")

A *db folder* holds the ``.hdt`` tree, a ``map.json`` naming which files make up
each URN, and (once built) a ``map.catalog.json`` of per-file statistics. File
paths inside ``map.json`` are relative to that folder.

This subpackage is deliberately isolated from ``pyhdtkit.hdt`` — the conversion
half of the package — and imports nothing from it. See the ``ponytail:`` note in
``pyhdtkit.sparql.hdt.binio`` for why the shared-looking primitives are not
shared.
"""

from __future__ import annotations

from pathlib import Path

from .catalog import Catalog, CatalogBuilder, FileStats, UrnStats
from .hdt import HDTFile, decode_term, encode_term
from .mapping import Mapping, MappingError, MappingLoader
from .optimize import prepare
from .store import HDTStore
from .store.hdt_store import query
from .store.hdt_store import dataset as _dataset_from_mapping

MAPPING_NAME = "map.json"

__all__ = [
    "dataset", "query", "build_catalog", "prepare",
    "HDTStore", "HDTFile", "decode_term", "encode_term",
    "Mapping", "MappingError", "MappingLoader",
    "Catalog", "CatalogBuilder", "FileStats", "UrnStats",
    "MAPPING_NAME",
]


def mapping_path(target: str | Path) -> Path:
    """Resolve a db folder to its mapping file.

    Accepts either the folder (``"mydb/"``) or the mapping itself
    (``"mydb/map.json"``), so callers need not care which they hold.
    """
    path = Path(target)
    return path / MAPPING_NAME if path.is_dir() else path


def dataset(target: str | Path = MAPPING_NAME, default_union: bool = True,
            catalog: Catalog | None = None):
    """Open a db folder (or a mapping file) as a queryable rdflib Dataset.

    ``default_union=True`` lets a query without a GRAPH clause see every URN.
    """
    return _dataset_from_mapping(
        mapping_path(target), default_union=default_union, catalog=catalog
    )


def build_catalog(target: str | Path = MAPPING_NAME) -> Catalog:
    """Build the per-file statistics catalog and write it into the db folder.

    Writes ``map.catalog.json`` beside the mapping. The catalog is what lets a
    query skip whole files it cannot match, so building it is worth doing once
    per corpus; queries still return correct answers without it, just by
    opening more files.
    """
    mapping = MappingLoader(mapping_path(target)).load()
    catalog = Catalog.load(mapping, save=True)
    catalog.save()
    return catalog
