<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Shyam-Sundar-Reddy/pyhdtkit/main/docs/logo-wordmark-dark.svg">
  <img src="https://raw.githubusercontent.com/Shyam-Sundar-Reddy/pyhdtkit/main/docs/logo-wordmark-light.svg" alt="pyhdtkit" height="60">
</picture>

[![Tests](https://github.com/Shyam-Sundar-Reddy/pyhdtkit/actions/workflows/ci.yml/badge.svg)](https://github.com/Shyam-Sundar-Reddy/pyhdtkit/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/pyhdtkit.svg)](https://pypi.org/project/pyhdtkit/)
[![PyPI license](https://img.shields.io/pypi/l/pyhdtkit.svg)](https://pypi.org/project/pyhdtkit/)

A pure-Python package for RDF HDT files. It does two things:

**Convert** between Turtle (`.ttl`) and HDT (`.hdt`)

- `.ttl` → `.hdt`
- `.hdt` → `.ttl`
- combine two or more `.hdt` files into one

**Query** HDT files with SPARQL 1.1 — without reading them

- point it at a folder of `.hdt` files plus a `map.json` naming your graphs
- ask a question, get an answer back without decoding files it doesn't need

No CLI — `import pyhdtkit` is the interface. No Rust, no native extension.

## Install

```bash
pip install pyhdtkit
pip install "pyhdtkit[fast]"   # optional CRC speedup, see Performance
```

Dev:

```bash
pip install -e ".[dev]"
```

## Usage — converting

```python
from pyhdtkit import ttl2hdt, hdt2ttl, hdtcat

ttl2hdt("graph.ttl", "graph.hdt")
hdt2ttl("graph.hdt", "graph.ttl")
hdtcat(["a.hdt", "b.hdt"], "combined.hdt")
```

## Usage — querying

Put your `.hdt` files in a folder with a `map.json` saying which files make up
each named graph. Paths are relative to that folder, and nested subfolders carry
no meaning — every file listed under a URN belongs to that one graph.

```
mydb/
  map.json
  map.catalog.json        # built once, see below
  hdt/sd/sd1/sd1_sample1.hdt
  hdt/sd/sd1/sd1_sample2.hdt
```

```json
{
  "urn:hdt:sd":    ["hdt/sd/sd1/sd1_sample1.hdt", "hdt/sd/sd1/sd1_sample2.hdt"],
  "urn:hdt:kafka": ["hdt/kafka/kafka1/kafka1_sample1.hdt"]
}
```

```python
from pyhdtkit import dataset, build_catalog

build_catalog("mydb/")          # once per corpus, writes mydb/map.catalog.json
ds = dataset("mydb/")

for s, p, o in ds.query("""
        SELECT ?s ?p ?o
        WHERE { GRAPH <urn:hdt:sd> { ?s ?p ?o } }
        LIMIT 10"""):
    print(s, p, o)
```

The mapping is the only source of truth: folders are never scanned, so a file
not listed does not exist as far as queries are concerned. A mapping naming a
missing file fails loudly at load rather than silently returning fewer results.

### Why it's fast

It never reads the files. A query is answered by seeking into them:

- the **catalog** records which predicates live in which file, so a query for a
  rare predicate opens only the files that can contain it — and one for an
  absent predicate opens none at all
- each file's dictionary is sorted, so a term is found by binary search instead
  of scanning
- the triples are indexed by a rank/select bitmap, so a subject is reached by
  arithmetic rather than by counting through the file

Measured on the test corpus (9 files in one graph):

| Query | Files opened |
|---|---|
| Rare predicate | **1** of 9 |
| Predicate in no file | **0** of 9 |
| `LIMIT 3` over the graph | **1** of 9 |
| `len(store)` (count everything) | **0** of 9 |

Results stream, so `LIMIT` costs what it asks for rather than what the corpus
holds.

The catalog is an optimisation, not a requirement — queries return the same
answers without it, they just open more files. Build it when the data changes;
it is validated against each file's size and mtime and rebuilt when stale.

**Known limits.** Only `SELECT`/`ASK`/`CONSTRUCT`/`DESCRIBE` reads — the store
is read-only, and `SERVICE` federation is not supported. HDT indexes subjects
only, so patterns with a bound predicate or object (`?s :p ?o`, `?s ?p :o`) scan
the candidate files rather than seeking within them.

## Errors

The three conversion functions raise `ValueError` for anything that goes wrong —
a missing or unreadable input file, malformed Turtle, a truncated or corrupt
`.hdt` file, or an unwritable output path. `hdtcat` additionally requires
at least 2 input paths.

On the query side a bad mapping raises `MappingError` at load, naming the URN
and path — a mapping that quietly skipped a graph would return wrong answers,
which is worse than failing.

## Status

Conversion and querying are both implemented: a real HDT binary reader and
writer (dictionary front-coding, BitmapTriples), built from scratch — no Rust,
no C extension, no wrapping an existing HDT library. `rdflib` handles the Turtle
grammar and the SPARQL language; everything HDT-specific is pure Python.

The read path (`hdt2ttl`) is verified against a real `.hdt` file produced
by independent hdt-cpp tooling (`tests/fixtures/snikmeta.hdt`), not just
against our own writer.

The two halves are kept deliberately separate — `pyhdtkit.hdt` (convert) and
`pyhdtkit.sparql` (query) import nothing from each other, and a test enforces
that. They need different things from the same format: conversion reads whole
files, querying must never read a whole file. Keeping them apart costs a little
duplicated low-level code and means a change to one can't break the other; it
also lets the test suite cross-check two genuinely independent decoders against
each other.

## Performance

HDT's compactness comes from succinct bit-level structures (rank/select
bitmaps, front-coded dictionaries) that are naturally suited to compiled
languages. This is pure Python — it will be slower and more memory-hungry
than the reference C++ (`hdt-cpp`) or a Rust implementation, especially at
large triple counts. That's an accepted, deliberate trade-off for this
package: correctness and hackability over raw speed.

Measured on this machine (`benchmarks/bench.py`, synthetic triples,
default front-coding block size):

| Triples   | Write  | Read   | Write `[fast]` | Read `[fast]` | File size |
|-----------|--------|--------|----------------|---------------|-----------|
| 1,000     | 0.01s  | 0.00s  | 0.00s          | 0.00s         | 0.01 MB   |
| 10,000    | 0.05s  | 0.03s  | 0.04s          | 0.02s         | 0.07 MB   |
| 100,000   | 0.60s  | 0.29s  | 0.53s          | 0.19s         | 0.79 MB   |
| 1,000,000 | 7.2s   | 3.2s   | 6.1s           | 1.9s          | 8.4 MB    |

Roughly linear scaling.

### Optional speedup

```bash
pip install "pyhdtkit[fast]"
```

This pulls in `google-crc32c` — the `[fast]` columns above. HDT checksums
every section it writes, and a pure-Python CRC loop is ~2600x slower than
a compiled one, which made it the single largest cost in the read path
once everything else was tuned.

Being precise about what this is: `google-crc32c` wraps
[`google/crc32c`](https://github.com/google/crc32c), a **compiled C++
library**. It ships as a prebuilt wheel for CPython 3.9–3.14 on Windows
x64, macOS (Intel/ARM), and glibc Linux (x86_64/i686/aarch64), so you
don't need a compiler — but there is compiled C++ running under the hood,
and there is **no musl wheel**, so on Alpine this extra would try to build
from source.

None of that touches the default install: `pip install pyhdtkit` pulls
only `rdflib` (itself pure Python) and contains zero compiled code. The
pure-Python CRC stays the fallback, and the test suite pins the two
implementations to identical output and runs green in both modes.

Only the checksum is ever delegated — all HDT encoding and decoding is our
own Python code either way.

### Notes on what makes it fast

- Bit-packing streams through a small bounded buffer rather than shifting
  one whole-array Python integer, which would be O(n²) (`binio.py`'s
  `pack_lsb_bitfields`/`unpack_lsb_bitfields`).
- Bitmaps (1 bit per entry, the largest arrays in a typical file) get a
  byte-at-a-time fast path instead of a per-bit loop.
- Dictionary front-coding finds shared prefixes via a single big-integer
  XOR instead of comparing bytes one at a time.

No numpy or other compiled-array dependency was needed; one may get added
later if profiling on a real workload shows it's worth the weight.
