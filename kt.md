# Build Prompt — `urn_hdt`

Hand this whole file to an agent as the prompt. It contains the goal, the
decisions already settled, the traps that cost real time, and the exact numbers
to verify against. Everything below is knowledge that was expensive to acquire
the first time — treat it as given, not as something to rediscover.

---

## The task

Build a Python package, `urn_hdt`, that answers **SPARQL 1.1 queries directly
against HDT files**, addressed by URN.

```python
import urn_hdt
ds = urn_hdt.dataset("map.json")
ds.query("SELECT * { GRAPH <urn:hdt:sd> { ?s ?p ?o } }")
```

Read-only. No conversion step, no intermediate graph, no loading a file that a
query does not need.

### Inputs you are given

1. A folder of `.hdt` files in a nested tree, e.g. `hdt/<domain>/<group>/<name>.hdt`
2. A JSON mapping file, URN → list of `.hdt` paths:

```json
{
  "urn:hdt:bian":  ["hdt/bian/bian1/bian1_sample1.hdt", "..."],
  "urn:hdt:kafka": ["..."],
  "urn:hdt:sd":    ["..."]
}
```

### Non-negotiables

- **rdflib owns the SPARQL language.** Do not write a parser, a join engine, or
  an algebra evaluator. Implement `rdflib.store.Store` and let rdflib drive.
- **The mapping is the single source of truth.** Never scan folders to discover
  URNs. Not in the mapping ⇒ does not exist.
- **Nested subfolders carry no semantics.** Every file under a URN lands in that
  one graph regardless of depth. There is no sub-graph.
- **Read-only.** Mutation raises. HDT is immutable.
- **Fail loudly.** A mapping naming a missing file is a broken mapping — raise
  at load, naming the URN and path. A silently skipped graph returns *wrong
  answers* instead of an error, which is far worse than a crash.
- **Pure Python, no compiled dependency.** `rdflib` is the only runtime dep.
- Out of scope: `SERVICE` (federation), SPARQL Update, TTL→HDT ingestion.

---

## Critical prior knowledge — read before writing code

These are the things that go wrong. Each one cost time to find.

### The HDT binary format

A file is: **Global control info → Header control info → header RDF payload →
Dictionary → Triples**, ending at EOF.

**Control Information block** (precedes each section):
```
"$HDT"      4 bytes, no null terminator
type        1 byte  (1=Global 2=Header 3=Dictionary 4=Triples)
format      null-terminated string
properties  "key=value;key=value;" null-terminated
CRC16       2 bytes LE, over "$" through the properties' null, inclusive
```

The header block's `length` property is the byte size of the RDF payload that
follows it. **Skip those bytes** — nothing in the query path needs them.
Files written by `pyhdtkit` have `length=0`, so the payload is empty; do not
assume it is populated.

**Checksums — none of these are stdlib defaults:**

| | Algorithm | Trap |
|---|---|---|
| CRC8 | poly `0x07`, init 0, no reflection | — |
| CRC16 | CRC-16/ARC (`0xA001` reflected, init 0) | — |
| CRC32 | **CRC-32C / Castagnoli** (`0x82F63B78` reflected) | **NOT `zlib.crc32`** — that is standard CRC-32 and will not match |

**VByte varint:** base-128, but the continuation bit is **inverted** relative to
LEB128 — continuation bytes have the high bit **clear**, the final byte has it
**set**.

**LogSequence2 array** — `[type][numbits][numentries vbyte][crc8][packed][crc32c]`.
Fields are `numbits` wide, packed LSB-first. **Random access is O(1) bit
arithmetic** — this is what lets you read entry 4,000,000 without the
3,999,999 before it. Do not decode it into a list.

**Bitmap** — same shape but `[type][totalbits vbyte][crc8][packed][crc32c]`,
no numbits byte (1 bit per entry by definition).

**FourSectionDictionary** — four Plain-Front-Coding sections in fixed order:
`shared`, `subjects`, `predicates`, `objects`.

```
[type][numstrings][nbytes][blocksize][crc8][block offsets: LogArray][string buffer][crc32c]
```

Strings are **byte-lexicographically sorted**; ID = 1-based rank. Each block
(default 16 strings) stores its **first string whole**, null-terminated; the
rest are front-coded as `[shared-prefix-length vbyte][suffix][null]`.

Two consequences you must exploit:
- **`id_of(term)`**: binary-search the blocks on their whole first strings, then
  front-decode *one* block. Never expand the section.
- **`string_at(id)`**: jump to block `(id-1)//blocksize`, decode up to that entry.

> **Trap:** the shared-prefix length is a **byte** count, not a character count.
> Front-coding operates on raw UTF-8. Slicing a decoded `str` corrupts any term
> with a multi-byte character.

**ID spaces:** terms used as *both* subject and object live in `shared` and take
the low IDs in **both** spaces. So `subject_id = shared.id_of(t) or (len(shared) + subjects.id_of(t))`,
same for objects. Predicates have their own independent space.

**BitmapTriples** — SPO adjacency, and **subject IDs are never written down**:
- `ArrayY`: one predicate ID per (S,P) pair. `BitmapY`: bit set on the **last
  pair of each subject**.
- `ArrayZ`: one object ID per triple. `BitmapZ`: bit set on the **last object of
  each (S,P) pair**.

A bulk decoder recovers subjects by counting bits front-to-back — which costs
the whole file. **The bitmaps are a rank/select structure**, so instead:

```
subject of pair y     = rank1(BitmapY, y) + 1
pairs of subject s    = [select1(s-1)+1 .. select1(s)]     (0 as start when s == 1)
objects of pair y     = [select1_z(y)+1 .. select1_z(y+1)] (0 as start when y == 0)
```

That is the entire seeking mechanism. Build the rank index once by popcounting
**bytes** (C-level, never per-bit Python) with a cumulative counter every 512
bits; `select1` binary-searches that index then walks ~64 bytes.

Only `order=1` (SPO) exists in practice. **Raise on any other order** — a
different order puts IDs in different slots and would silently return wrong
triples.

> **What SPO order costs you:** `(s,?,?)` seeks. `(?,p,?)` and `(?,?,o)` are
> scans — HDT indexes subjects only. The real fix is HDT-FoQ's extra PSO/OPS
> indexes in a separate `.index` file. The catalog (below) prunes at a coarser
> level for far less work.

### Terms

**HDT writes IRIs bare — no angle brackets.** A term string is therefore *not*
valid N3 on its own.

```python
def decode_term(raw):
    return from_n3(raw) if raw[:1] in '"_' else URIRef(raw)

def encode_term(term):
    return str(term) if isinstance(term, URIRef) else term.n3()
```

> **Trap 1:** `rdflib.util.from_n3("http://example.org/x")` raises
> `KeyError: 'http'` — it reads a bare IRI as a prefixed name.
> **Trap 2:** wrapping *every* term in `URIRef` is the obvious shortcut and
> produces a graph where `?tier = "1"` silently never matches. Write a test that
> fails on exactly this.

### rdflib specifics

- **`Store.triples` is called with `context` as a KEYWORD**:
  `store.triples((s,p,o), context=self)`. A wrapper signature using any other
  parameter name silently yields zero rows.
- It must yield `((s, p, o), context_iterator)` — not bare triples.
- **`Store.add_graph` must be a silent no-op.** `Dataset.__init__` creates its
  default graph through it; raising there makes a read-only store unusable.
- Implement `bind`/`prefix`/`namespace`/`namespaces` — the base `Store`'s are
  no-ops and rdflib binds prefixes through the store.
- Use `Dataset(store=..., default_union=True)` so a query with no `GRAPH` clause
  sees every URN.
- **`Dataset.contexts()` is deprecated** — use `.graphs()`. Note `.graphs()` also
  yields the default graph, identifier `urn:x-rdflib:default`; filter on your own
  URN prefix, not on `urn:`.
- **rdflib already reorders BGPs** (`algebra.reorderTriples`) — but only by
  *count of bound terms*, a shape heuristic. It cannot know one predicate matches
  72 triples and another matches 1. Same-shape patterns tie and keep written
  order. That tie is exactly what your catalog breaks.
- Reorder by preparing the query (`translateQuery(parseQuery(q))`), rewriting
  `BGP` nodes in the algebra tree, and passing the `Query` object to
  `ds.query()`. Never fork rdflib's evaluator.

### `pyhdtkit` (if present)

Useful **only** for TTL→HDT conversion (`ttl2hdt`), and as an independent
reference decoder to cross-check against in tests. Keep it out of the runtime
dependencies. Its `read_hdt()` returns the whole file as a list and offers no
`search()` — building on it caps you at bulk decoding, which is the one thing
this engine exists to avoid.

---

## Build order

Each step lands with its own test file. Do not batch the testing to the end.

**1 · Data model** → `docs/data_model.md`
Freeze the contract before any code: URN = named graph, storage = one or more
`.hdt` files, JSON is the sole source of truth, read-only, fail loudly. Document
the schema, that paths resolve **relative to the mapping file's directory**, the
term encoding, and that nested folders have no semantics.

**2 · Mapping loader** → `urn_hdt/mapping/{loader,models}.py`
`MappingLoader(path).load()` → `Mapping` with `urns()`, `files_for()`,
`contains()`. Validate: file exists, `.hdt` suffix, no duplicate URNs, value is
a non-empty list of strings, document is an object, JSON parses.

> **Duplicate URNs need `json.load(..., object_pairs_hook=...)`.** Plain
> `json.load` silently keeps the last value — a whole graph's files vanish with
> no error.

All-or-nothing: one bad entry fails the entire load.

**3 · HDT reader** → `urn_hdt/hdt/{binio,header,dictionary,triples,reader}.py`
Per the format section above. `mmap` the file so the OS pages in only what the
seeks touch. `HDTFile.search(s, p, o)` with `None` as wildcard, streaming.

> A bound term absent from the dictionary must **short-circuit to zero results
> without reading the triples section at all.**

Do **not** verify CRCs on open — that reads the entire file, defeating the whole
design. Check header CRCs (bounded, cheap); expose `verify()` as an explicit
full pass.

**4 · Catalog** → `urn_hdt/catalog/{builder,models,store}.py`
Statistics **per file**, not per URN — per-URN is only enough to pick a graph;
per-file is what lets you *skip files*. Record `path`, `size`, `mtime`,
`triple_count`, `subject_count`, `object_count`, `predicates{name: count}`.

Get them from metadata only, never by decoding triples:
- triple count ← triples section header
- distinct subjects/objects ← `len(shared) + len(subjects|objects)` from the dictionary
- predicate counts ← walk `ArrayY` measuring each object run by its `BitmapZ`
  boundaries. Integers only, no dictionary lookups.

Cache to JSON beside the mapping, **written atomically** (temp + replace).
Validate on load against size+mtime and the current file list; a stale or corrupt
cache is discarded and rebuilt, never fatal — stale stats would prune away files
that *do* hold matches. Provide `rebuild(urn=None)`.

**5 · Store adapter** → `urn_hdt/store/hdt_store.py`
`HDTStore(Store)` with `context_aware = graph_aware = True`. Implement
`triples()`, `contexts()`, `__len__` (served from the catalog). **No join logic
here.** Three levels of doing less work:

1. catalog names which files can hold the pattern's predicate → the rest are never opened
2. a bound term absent from a file's dictionary → that file ends immediately
3. a bound subject → seek instead of scan

> **Trap:** deduplicating triples across a URN's files with a `seen` set makes
> that set grow with the result size, silently destroying streaming on a large
> graph. **Skip dedup entirely when only one file can match** — the common case
> after pruning.

Cache the `Graph` handle per URN. Provide `close()` releasing every mapping.

**6 · Reordering** → `urn_hdt/optimize/reorder.py`
Estimate rows per pattern from the catalog: start at the predicate's count (or
the graph's size if unbound), then divide by `subject_count` / `object_count`
for each bound subject/object. A bound predicate no file contains estimates
**0** — sorts first, empties the BGP before anything is read.

Order **greedily, not by pure sort**: cheapest first, then repeatedly the
cheapest pattern sharing a variable with what is already ordered. A pure cost
sort can pick two patterns sharing no variable and force a cartesian product.

Walk the whole algebra tree so BGPs inside `OPTIONAL`/`UNION`/`MINUS`/subqueries
are covered; a `GRAPH <urn>` node narrows the statistics beneath it to that URN.

**7 · Streaming** — verify, don't add code. Generators end to end; work must
track the `LIMIT`, not the corpus.

**8 · CLI** → `urn_hdt/cli.py`
`inspect [urn] [--predicates]`, `query (--sparql FILE | -q TEXT) [--format table|tsv|csv|json]`,
`rebuild-catalog [urn]`. Handle `ASK` (`result.type == "ASK"`) and
`CONSTRUCT`/`DESCRIBE` (`result.graph.serialize`) separately from `SELECT`.
Errors → stderr, exit 1. Deliberately narrow: no `register`, `scan`, or `ingest`.

**9 · Optimization + benchmark** → `benchmarks/bench.py`
Measure **work** (store reads, files opened), not just wall time — at small
corpus sizes interpreter overhead swamps the signal and memory deltas prove
nothing.

---

## Verification — do this, it caught real bugs

1. **Cross-check the decoder against an independent one.** Every file must
   decode byte-identically to `pyhdtkit.read_hdt` (dev-only dep). This is the
   single highest-value test.
2. **Every seek must equal filtering a full scan** — for every subject, every
   predicate, every object, on every file.
3. **Rank/select must equal a plain bit count** at every index.
4. **`id_of` and `string_at` must be inverses** across all four sections.
5. **Derive expected constants from the source data, not from the engine.**
   `grep` the Turtle sources. Three of my expected numbers were wrong and the
   engine was right — rubber-stamping its output would have hidden a real bug
   just as easily.
6. **Prove laziness by inspecting state**, not by timing: assert which files are
   open after a query.
7. **Test data must be differentiated.** Identical fixture files make catalog,
   reordering, and cross-graph tests pass no matter what the code does. Fixtures
   need distinct content per file, **skewed predicate frequencies** (aim for
   ~100:1 between most and least common), and shared IRIs across graphs so
   cross-graph joins return real rows.

### Expected results on the reference corpus

27 files, 3 URNs of 9 files, 1,218 triples.

| URN | files | triples | predicates | most common → rarest |
|---|---|---|---|---|
| `urn:hdt:bian` | 9 | 505 | 7 | 144 → 1 |
| `urn:hdt:kafka` | 9 | 460 | 9 | 72 → 1 |
| `urn:hdt:sd` | 9 | 253 | 9 | 63 → 1 |

Behaviour that must hold:

| Check | Expected |
|---|---|
| Rare predicate, 9-file URN | opens **1/9** files |
| Predicate in no file | opens **0/9** files, 0 rows |
| BGP reorder, selectivity 72/72/1 | **74 → 3** store reads, identical answers |
| `LIMIT 10` on a 505-triple graph | 10 store reads, 1 file opened |
| Subject seek vs full scan (70-triple file) | **25 vs 200** array reads |
| Absent term | **3** array reads, triples section untouched |
| `len(store)` | opens **0** files |

---

## Known limitations — state them, don't hide them

- `(?,p,?)` and `(?,?,o)` scan each candidate file. SPO order indexes subjects
  only; the real fix is HDT-FoQ PSO/OPS indexes.
- The per-file `id → term` memo cache is **unbounded** — it grows with distinct
  terms touched and nothing evicts it. Cap it with an LRU for large corpora.
- Catalog `triple_count` sums files, so it is an upper bound on distinct triples
  when a URN's files overlap. Deduplicating would mean reading everything.
- Full CRC verification is opt-in via `verify()`, not automatic on open.
- Only `FourSectionDictionary` + `BitmapTriples` + `order=1` are read. Anything
  else raises rather than guessing.

Mark every deliberate shortcut with a comment naming **the ceiling and the
upgrade path**, so the next reader knows it was a decision rather than an
oversight.

---

## Shape of the finished package

```
docs/data_model.md
benchmarks/bench.py
urn_hdt/
  __init__.py        dataset(), query(), public names
  cli.py             inspect / query / rebuild-catalog
  mapping/           loader.py  models.py
  hdt/               binio.py  header.py  dictionary.py  triples.py  reader.py
  catalog/           builder.py  models.py  store.py
  store/             hdt_store.py
  optimize/          reorder.py
tests/               one file per phase, incl. test_sparql_language.py + test_end_to_end.py
```

~2,000 lines of package, ~1,500 of tests, ~200 tests.

The finished engine answers any valid SPARQL 1.1 query except `SERVICE`:
property paths, subqueries, aggregates, `BIND`/`VALUES`, `MINUS`, `EXISTS`,
`OPTIONAL`, `UNION`, `FILTER`, `ASK`, `CONSTRUCT`, `DESCRIBE`, and cross-graph
`GRAPH` joins — reading nothing but the HDT bytes it actually needs.
