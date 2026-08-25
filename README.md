# pyhdtkit

A pure-Python package to convert between RDF Turtle (`.ttl`) and HDT (`.hdt`):

- `.ttl` → `.hdt`
- `.hdt` → `.ttl`
- combine two or more `.hdt` files into one

No CLI — `import pyhdtkit` is the interface. No Rust, no native extension.

## Install (dev)

```bash
pip install -e ".[dev]"
```

## Usage

```python
from pyhdtkit import ttl2hdt, hdt2ttl, hdtcat

ttl2hdt("graph.ttl", "graph.hdt")
hdt2ttl("graph.hdt", "graph.ttl")
hdtcat(["a.hdt", "b.hdt"], "combined.hdt")
```

## Errors

All three functions raise `ValueError` for anything that goes wrong — a
missing or unreadable input file, malformed Turtle, a truncated or corrupt
`.hdt` file, or an unwritable output path. `hdtcat` additionally requires
at least 2 input paths.

## Status

All three functions are implemented: a real HDT binary reader and writer
(dictionary front-coding, BitmapTriples), built from scratch — no Rust, no
C extension, no wrapping an existing HDT library. `rdflib` handles Turtle
parsing/serialization; everything HDT-specific is pure Python.

The read path (`hdt2ttl`) is verified against a real `.hdt` file produced
by independent hdt-cpp tooling (`tests/fixtures/snikmeta.hdt`), not just
against our own writer.

## Performance

HDT's compactness comes from succinct bit-level structures (rank/select
bitmaps, front-coded dictionaries) that are naturally suited to compiled
languages. This is pure Python — it will be slower and more memory-hungry
than the reference C++ (`hdt-cpp`) or a Rust implementation, especially at
large triple counts. That's an accepted, deliberate trade-off for this
package: correctness and hackability over raw speed.

Measured on this machine (`benchmarks/bench.py`, synthetic triples,
default front-coding block size):

| Triples   | Write   | Read   | File size |
|-----------|---------|--------|-----------|
| 1,000     | 0.01s   | 0.00s  | 0.01 MB   |
| 10,000    | 0.06s   | 0.03s  | 0.07 MB   |
| 100,000   | 0.70s   | 0.34s  | 0.79 MB   |
| 1,000,000 | 8.4s    | 3.5s   | 8.4 MB    |

Roughly linear scaling. The bit-packing routines were rewritten early on to
avoid an O(n²) trap (repeatedly shifting one big Python integer instead of
streaming through a small bit buffer) — see `binio.py`'s
`pack_lsb_bitfields`/`unpack_lsb_bitfields` — which is what makes the
numbers above hold up past a few thousand triples. No numpy or other
compiled-array dependency was needed to get here; one may get added later
if profiling on a real workload shows it's worth the extra dependency
weight.
