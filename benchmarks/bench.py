"""Encode/decode timing at a few triple-count sizes. Not a test — a
dev-time script to produce the numbers documented in README.md's
performance section. Run with: uv run python benchmarks/bench.py
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from pyhdtkit.hdt.reader import read_hdt, write_hdt


def synthetic_triples(n: int) -> list[tuple[str, str, str]]:
    rng = random.Random(42)
    # A handful of distinct subjects/predicates/objects reused across many
    # triples (realistic RDF graphs are far from all-unique terms) — plus
    # every triple is unique so none get de-duplicated away.
    predicates = [f"http://example.org/p{i}" for i in range(20)]
    return [
        (f"http://example.org/s{i % (n // 10 + 1)}", rng.choice(predicates), f"http://example.org/o{i}")
        for i in range(n)
    ]


def bench(n: int) -> None:
    triples = synthetic_triples(n)
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "bench.hdt"

        t0 = time.perf_counter()
        write_hdt(triples, path)
        write_s = time.perf_counter() - t0

        size_mb = path.stat().st_size / 1_000_000

        t0 = time.perf_counter()
        read_hdt(path)
        read_s = time.perf_counter() - t0

    print(f"{n:>9,} triples  write {write_s:7.2f}s  read {read_s:7.2f}s  file {size_mb:6.2f} MB")


if __name__ == "__main__":
    from pyhdtkit.hdt.binio import _crc32c_accelerated

    mode = "C-backed CRC-32C" if _crc32c_accelerated is not None else "pure Python"
    print(f"crc32c: {mode}")
    for n in (1_000, 10_000, 100_000, 1_000_000):
        bench(n)
