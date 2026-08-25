# pyhdtkit

A pure-Python package to convert between RDF Turtle (`.ttl`) and HDT (`.hdt`):

- `.ttl` → `.hdt`
- `.hdt` → `.ttl`
- combine two or more `.hdt` files into one

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

This pulls in `google-crc32c`, a prebuilt-wheel CRC-32C (no compiler or
Rust toolchain needed on your machine) — the `[fast]` columns above. HDT
checksums every section it writes, and a pure-Python CRC loop is ~2600x
slower than the C one, which made it the single largest cost in the read
path once everything else was tuned. Everything still works without it,
just slower; the pure-Python implementation stays the fallback and the two
are pinned to identical output by the test suite.

Only the checksum is delegated — all HDT encoding/decoding is our own
Python code either way.

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
