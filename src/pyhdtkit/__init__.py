from pyhdtkit.convert import hdt2ttl, hdtcat, ttl2hdt
from pyhdtkit.sparql import build_catalog, dataset, query

__version__ = "0.4.0"

__all__ = [
    "__version__",
    # convert: Turtle <-> HDT
    "ttl2hdt", "hdt2ttl", "hdtcat",
    # query: read-only SPARQL over a db folder of HDT files
    "dataset", "query", "build_catalog",
]
