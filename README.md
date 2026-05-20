# ChunkHound Index Compactor

Compact a [DuckDB](https://duckdb.org) database by rebuilding it into a fresh file. The motivating use case was shrinking a bloated [ChunkHound](https://github.com/chunkhound/chunkhound) index (whose per-batch HNSW re-serialization leaves large amounts of orphaned-but-counted blocks), but the implementation is fully generic; it works on any single-schema DuckDB file.

## ⚡ Quick Start

```bash
uvx chunkhound-index-compactor path/to/db.duckdb
# writes path/to/db.duckdb.compacted

uvx chunkhound-index-compactor path/to/db.duckdb --replace
# swaps in the compacted copy and keeps the original at path/to/db.duckdb.bak

uvx chunkhound-index-compactor path/to/db.duckdb --skip-hnsw
# skips rebuilding vector indexes (RAM-flat, smallest output); restore them later
uvx chunkhound-index-compactor restore path/to/db.duckdb.compacted
```

The source is opened read-only, but an active writer holds the file lock. Close any process writing to the database before running.

## 🖥️ CLI Usage

```
$ chunkhound-index-compactor --help
Usage: chunkhound-index-compactor [OPTIONS] COMMAND [ARGS]...

Commands:
  compact  Compact a DuckDB database by rebuilding it into a fresh file. (default)
  restore  Rebuild HNSW vector indexes in a --skip-hnsw artifact, in place.
```

A bare invocation routes to `compact`, so `chunkhound-index-compactor SOURCE` still works:

```
chunkhound-index-compactor SOURCE [TARGET] [--replace] [--skip-hnsw]
chunkhound-index-compactor restore DATABASE
```

| Argument / Option | Meaning                                                                           |
|-------------------|-----------------------------------------------------------------------------------|
| `SOURCE`          | Path to the existing DuckDB file (required)                                       |
| `TARGET`          | Path for the compacted output [default: `<source>.compacted`]                     |
| `--replace`       | After success, replace source with the compacted file (original → `<source>.bak`) |
| `--skip-hnsw`     | Do not rebuild vector indexes; write a recipe table for later `restore`           |

With `--skip-hnsw`, the output has no vector index and falls back to a brute-force scan (correct, just unaccelerated) until you run `restore`. Rebuilding the HNSW is the memory-dominant step, so `--skip-hnsw` lets you compact on a small machine and `restore` on a RAM-capable one. See [docs/benchmarks.md](docs/benchmarks.md) for peak-RAM numbers and [docs/architecture.md §RAM cost asymmetry](docs/architecture.md#ram-cost-asymmetry) for why.

## 🐍 Library Usage

```python
from pathlib import Path
from chunkhound_index_compactor import compact_database, restore_indexes, replace_with_compacted

result = compact_database(Path("big.duckdb"), Path("small.duckdb"))
print(f"{result.source_size} -> {result.target_size} ({result.delta_pct:+.1f}%)")

# Small-RAM path: skip the vector index, restore it later on a bigger machine.
compact_database(Path("big.duckdb"), Path("small.duckdb"), skip_hnsw=True)
restored = restore_indexes(Path("small.duckdb"))
print(f"restored: {restored.restored}")

# Optional: swap in place with .bak backup
backup = replace_with_compacted(result.source, result.target)
```

`compact_database()` raises:
- `ValueError`: `target` resolves to the same path as `source`, the source has a non-`main` schema or a view, or the FK graph has a cycle.
- `FileNotFoundError`: `source` does not exist.
- `FileExistsError`: `target` already exists.

`restore_indexes()` raises:
- `FileNotFoundError`: `database` does not exist.
- `ValueError`: `database` has no `_compactor_hnsw_recipe` table (not a `--skip-hnsw` artifact).

`replace_with_compacted()` raises `FileExistsError` if `<source>.bak` already exists. It refuses to overwrite an existing backup.

## 🚫 Not Supported

The tool fails hard rather than silently dropping anything it cannot reproduce:

- Non-`main` schemas and views (raise `ValueError`).
- Foreign-key cycles among tables (raise `ValueError`).
- HNSW tuning parameters other than `metric` (`M`, `M0`, `ef_construction`, `ef_search`); they are not recoverable from a built index and are rebuilt at the `vss` defaults.

See [docs/architecture.md](docs/architecture.md#not-supported-and-why) for the reasoning, and [docs/out-of-scope.md](docs/out-of-scope.md) for approaches considered and not pursued.

## 🏗️ Development

Setup, local checks, CI, and release process: [CONTRIBUTING.md](CONTRIBUTING.md).

## ⚖️ License

MIT

---

> [!NOTE]
> Yes, an AI wrote this README. And the code, the docs, the tests, and
> the `.claude/skills` it now uses to write the next round. Yes, a human
> told it to keep the emojis. The human has ADHD, which, as it turns
> out, means his brain was already doing attention re-routing and
> context-window thrashing before LLMs made it cool. They call him ...
> LLMartin. The emojis are a feature.
