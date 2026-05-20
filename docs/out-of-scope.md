# Out of Scope

Approaches that were considered against the motivating [ChunkHound](https://github.com/chunkhound/chunkhound) workload and deliberately not pursued. Carrying the list keeps a future maintainer from rediscovering each dead end and re-running the same evidence.

## DiskANN or alternative ANN backends

ChunkHound writes HNSW via `duckdb-extension-vss`. Switching to DiskANN (or another on-disk ANN structure) would change the index format, break ChunkHound's read path, and require a coordinated change in ChunkHound itself. The motivating workload is "compact what ChunkHound writes," not "replace the ANN."

## `PRAGMA hnsw_compact_index('<index>')`

vss provides an in-place HNSW compaction pragma that prunes tombstones from the index without rebuilding the whole database. Two problems for this tool's purpose:

1. It still loads the HNSW into RAM (so it doesn't help the small-RAM case `--skip-hnsw` exists for).
2. It doesn't reclaim space outside the HNSW. The ChunkHound bloat shape is orphaned-used blocks from past HNSW serializations, not live-but-tombstoned entries in the current one, so the pragma touches the wrong region.

The pragma is the right tool when an HNSW has accumulated deletes; it isn't the tool when the source database has accumulated orphaned blocks across many HNSW lifecycles.

## HNSW tuning beyond `metric` (`M`, `M0`, `ef_construction`, `ef_search`)

These knobs affect recall and build/query speed. They aren't recovered because:

- **They're not surfaced by the catalog or any pragma.** `duckdb_indexes().sql` strips the entire `WITH (...)` clause. `pragma_hnsw_index_info()` returns `catalog_name`, `schema_name`, `index_name`, `table_name`, `metric`, `dimensions`, `count`, `capacity`, `approx_memory_usage`, `levels`, and `levels_stats`. None of the build-time tuning knobs appear there. There's no way to read them off a built index.
- **They're dominated by upstream factors.** The embedding model and reranking strategy (ChunkHound's MultiHopStrategy) move recall far more than these knobs.
- **Defaults are sane.** vss defaults (`connectivity`/`M`, `expansion_add`/`ef_construction`, `expansion_search`/`ef_search`) work for the ChunkHound workload.

If you depended on tuned values, you have to recreate those indexes manually after compaction. The tool does not pretend it can preserve them.

## Schema evolution, multi-schema, views, exotic columns

The tool refuses non-`main` schemas, views, user-defined types, generated columns, self-referential foreign keys, and HNSW indexes on non-bare-column expressions at the front gate. Reasons:

- Reproducing non-`main` schemas requires emitting `CREATE SCHEMA` before tables and rewriting `REFERENCES` to be schema-qualified.
- Reproducing views requires resolving them against the rebuilt tables (which may run before the underlying tables are materialized).
- User-defined types are inlined into the captured CREATE TABLE DDL, so reproducing them as named types would require a second pre-pass over `duckdb_types()`.
- Generated columns are kept in the DDL's column list but rejected by `INSERT ... SELECT *`, so reproducing them needs an explicit column list per table and exclusion of virtual columns.
- A self-referential FK survives `duckdb_tables().sql` as invalid DDL: the FK clause gets dropped and the column list keeps a trailing comma. Reproducing it would require either a post-create `ALTER TABLE ... ADD CONSTRAINT` (which DuckDB does not support for FKs) or deferred constraints (not portable across DuckDB versions).
- Expression HNSW keys cannot be captured by the current single-pattern regex (`_HNSW_COLUMN_RE` truncates at the first inner `)`), and the recipe schema only carries a column string, not an arbitrary expression.
- Silently dropping any of these would corrupt the user's data model or break later queries against the rebuilt file.

ChunkHound's production index hits none of these (verified at v5.1.0), so the refusals don't affect the motivating workload. They're safety nets for misuse.

## Out-of-core HNSW build

vss requires the HNSW to fit fully in RAM at both build and query time. There is no streaming-build or mmap-backed path inside vss; the index either fits or doesn't. `--skip-hnsw` exists because that constraint is upstream of this tool. The workaround is to defer the build to a RAM-capable machine, not to invent an out-of-core build path.

## Resume after partial failure

The compaction pipeline has many failure points (source attach, schema reject, FK topo sort, per-table INSERT, index DDL replay, HNSW build, checkpoint). On any failure after the target file is attached, the partial target is unlinked. Resuming would mean carrying enough state to know which step failed and that everything before it was durable on disk; for the target workload (single CLI run, deterministic schema) this overhead doesn't pay back. A half-written multi-GB file is worse than nothing.

## `--memory-limit` and `--temp-dir` flags

DuckDB exposes `SET memory_limit = '<n>GiB'` and `SET temp_directory = '<path>'`. The tool could surface them as flags. It doesn't, because:

- The flags multiply the test matrix without changing any contract.
- DuckDB's own settings can be configured via env or a connection-level override outside the tool.
- The motivating workload either fits the rebuild in RAM or uses `--skip-hnsw`; intermediate spill behavior isn't on the user's critical path.

## `--strategy copy|rebuild`

Earlier iterations of this tool exposed `COPY FROM DATABASE` as a strategy alongside the rebuild. It's gone:

- `COPY` fails on the motivating workload (the FK race the rebuild path was built to avoid).
- COPY's only structural edge would have been HNSW byte-identity. The rebuilt HNSW reproduces ChunkHound's existing per-batch drift anyway, so there's no determinism win either.

One path, no flag, no choice to expose.

## `restore` as a flag rather than a command

`restore` runs on a different machine than `compact --skip-hnsw`, takes different inputs, and runs later. It is a distinct operation, not a mode of compaction. Surfacing it as `--restore` would couple two operations that production usage already separates.
