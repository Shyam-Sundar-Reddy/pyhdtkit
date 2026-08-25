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

## Status

Base scaffold. The public API shape above is stable, but conversion is not
implemented yet — each function currently raises `NotImplementedError`.
