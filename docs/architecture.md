# Architecture

## Why a custom rebuild instead of `COPY FROM DATABASE`

DuckDB's single-file format does not reclaim disk space after deletes or drops, and `VACUUM` is a no-op for size reclamation. The obvious workaround is to `ATTACH` the source and `COPY FROM DATABASE` into a fresh target. That path commits child rows before their foreign-key parents and aborts with a non-deterministic FK violation on FK-bearing [ChunkHound](https://github.com/chunkhound/chunkhound) indexes at scale ([duckdb/duckdb#16785](https://github.com/duckdb/duckdb/issues/16785)). The failing key differs between runs on the same source; the data is referentially clean. It is an insertion-order race, and DuckDB exposes no setting that disables FK enforcement.

`chunkhound-index-compactor` rebuilds the database table-by-table in foreign-key-topological order instead, which sidesteps the race.

What gets reclaimed is not free space. `pragma_database_size` on a real ChunkHound index reports almost no free blocks: the motivating workload showed 4 769 560 used vs 173 free on a 1.1 TiB file (see [benchmarks.md](benchmarks.md)). The bloat is *orphaned used blocks*: old HNSW serializations left behind by ChunkHound's drop-and-recreate index churn on write batches that meet its size threshold (50 rows by default; the `vss` extension re-serializes the whole HNSW on every CHECKPOINT). The catalog counts them as used because no live object references them; only a fresh-file rewrite drops them, because the rewrite copies live catalog objects only.

## Compaction pipeline

When you invoke `compact_database(source, target, *, skip_hnsw=False)`:

1. Open an in-memory DuckDB connection. Set `temp_directory` to a `.chunkhound-compactor.tmp` directory beside the target so any spill stays on the destination filesystem. DuckDB's default is the CWD-relative `.tmp`, which can fill a small working FS while the destination disk has room.
2. `ATTACH '<source>' AS src (READ_ONLY)`.
3. Refuse source shapes the rebuild cannot faithfully reproduce (non-`main` schemas, views, user-defined types, generated columns, self-referential FKs, HNSW indexes on non-bare-column expressions). Each refusal raises `ValueError` before the target file is touched.
4. Capture from the source:
   - Sequence DDL (`duckdb_sequences().sql`)
   - Table DDL (`duckdb_tables().sql`)
   - Index DDL (`duckdb_indexes().sql`)
   - Split HNSW indexes (`USING HNSW`) from the rest.
5. For each HNSW index, `LOAD` the bundled `vss` binary and recover the true `metric` from `pragma_hnsw_index_info()`. The catalog DDL strips the `WITH (...)` clause, so the metric is unrecoverable from `duckdb_indexes().sql` alone. The indexed column is parsed from the DDL.
6. Compute a foreign-key-topological order of the tables (parent before child). Cycles raise `ValueError`.
7. `ATTACH '<target>' AS dst`, `USE dst`. Replay sequences (their `START` value preserves the cursor; no `setval` needed), then create tables in topological order so unqualified `REFERENCES` resolve.
8. `SET preserve_insertion_order = false`; `INSERT INTO <table> SELECT * FROM src.<table>` one table at a time, parent before child. This one-table-at-a-time, parent-before-child insertion is what avoids the FK race that breaks `COPY FROM DATABASE`.
9. Replay non-HNSW index DDL verbatim. For HNSW:
   - `skip_hnsw=False`: `SET hnsw_enable_experimental_persistence = true`, then recreate each index `WITH (metric = '<recovered>')`.
   - `skip_hnsw=True`: write the `_compactor_hnsw_recipe` table instead (see below).
10. `CHECKPOINT dst`, `USE memory`, `DETACH` both, close the connection. Return `CompactionResult`.

On any failure after the target file is attached, the partial target is unlinked (along with the `<target>.wal` write-ahead log if a CHECKPOINT step left one behind) and the original exception re-raises. A half-written multi-GB file is worse than nothing.

SQL literals are built by string interpolation because DuckDB DDL does not accept parameter binding. Single quotes are doubled via `_escape_sql_literal()`; table and index names are wrapped via `_quote_identifier()`, which doubles any embedded `"`.

## RAM cost asymmetry

Reading the source never loads its HNSW into RAM. `LOAD 'vss'` is needed for `pragma_hnsw_index_info()` to recover the metric, but the streaming `INSERT INTO ... SELECT * FROM src.<table>` phase does not load the index. Scanning the source's tables works without the extension. The streaming-copy phase is RAM-flat.

Building the destination HNSW is what dominates peak RAM. The `vss` HNSW must fit fully in memory at build time, and on top of that vss allocates working memory proportional to `M`, `M0`, and `ef_construction`. As a rule of thumb on the motivating workload, peak RAM for a full rebuild lands around 3 to 4 times the source HNSW's `pragma_hnsw_index_info().approx_memory_usage`.

That asymmetry is what makes `--skip-hnsw` a small-RAM unlock and `restore` a separate-machine step. Skipping the HNSW build keeps the rebuild flat at the streaming-copy peak (a few GiB) regardless of source HNSW size. `restore` reproduces the full-rebuild peak on a RAM-capable machine later. Concrete numbers are in [benchmarks.md](benchmarks.md).

## The `_compactor_hnsw_recipe` table

`--skip-hnsw` records what it stripped in a `_compactor_hnsw_recipe` table inside the output; one self-contained file, no sidecar:

| Column        | Meaning                                          |
|---------------|--------------------------------------------------|
| `index_name`  | Name of the stripped HNSW index                  |
| `table_name`  | Table the index was on                           |
| `column_name` | Indexed column expression                        |
| `metric`      | Distance metric recovered at compaction time     |

`restore_indexes(database)` opens the file read-write, fails hard if `_compactor_hnsw_recipe` is absent (the file is not a `--skip-hnsw` artifact), `LOAD`s `vss`, and recreates each recipe index that does not already exist (idempotent) with its recorded metric. It returns `RestoreResult(database, restored)` where `restored` is the tuple of index names created this run.

Above some index size, `restore` is required for acceptable query latency; for moderate indexes the brute-force fallback is fine.

The table name is exposed as the `RECIPE_TABLE` constant in `core.py`. ChunkHound ignores the extra table.

## Bundled `vss` extension

DuckDB cannot create or read an HNSW index without the `vss` extension loaded. To keep the tool offline-safe and avoid the network round-trip of `INSTALL`, this package depends on the community-built [`duckdb-extension-vss`](https://pypi.org/project/duckdb-extension-vss/) wheel, which ships the `vss.duckdb_extension` binary as part of its payload. The binary is `LOAD`ed directly from disk via `_bundled_extension_path()` and `_load_bundled_extension()`; no `INSTALL`, no network. It is loaded only when the source actually contains an HNSW index.

| Source index type | Extension | Bundled by                                    |
|-------------------|-----------|-----------------------------------------------|
| `HNSW`            | `vss`     | `duckdb-extension-vss` (community-maintained) |

Only `metric` is recovered because it is correctness-affecting: a metric mismatch leaves the index dead and queries fall back to brute force. The other HNSW knobs (`M`, `M0`, `ef_construction`, `ef_search`) are not surfaced by any pragma, which is why they cannot be preserved.

## ChunkHound compatibility

The tool is structurally generic, but it was built against ChunkHound and a few details of ChunkHound's behavior shape the design:

- **ChunkHound writes HNSW with `metric = 'cosine'`.** The catalog DDL strips the `WITH (...)` clause, so a metric-blind rebuild would silently reset the index to the `vss` default `l2sq`. ChunkHound's cosine-distance queries would no longer hit the index and would run brute-force against the table. The metric-via-pragma recovery is the regression guard; `test_rebuild_preserves_hnsw_metric` enforces it.
- **ChunkHound rebuilds HNSW only on the write path.** Its read path runs vector-distance queries with no index-existence check and no `CREATE INDEX` branch. A `--skip-hnsw` artifact opened by ChunkHound brute-forces every semantic query forever until something rebuilds the index. That is why `restore` exists as a separate command: ChunkHound itself will not lazy-rebuild.
- **HNSW non-determinism is already part of ChunkHound's normal operation.** ChunkHound drops and recreates the HNSW on write batches that meet its size threshold (`insert_embeddings_batch`, default 50 rows), so the live index already changes shape across runs even without compaction. The rebuild reproduces that property; it does not introduce a new one.

## Not supported (and why)

These cases the code refuses or cannot reproduce. The first five are front-gate refusals in `_reject_unsupported_objects` and `_capture_hnsw_recipes`; each fires before `ATTACH dst`, so the target file is never created when one trips.

- **Non-`main` schemas and views.** Reproducing schemas requires `CREATE SCHEMA` ordering and qualified `REFERENCES` rewriting; reproducing views requires resolving them against the rebuilt tables. Silently dropping them would corrupt the user's data model.
- **User-defined types.** `duckdb_tables().sql` inlines ENUM/STRUCT/alias definitions when emitting CREATE TABLE, so without refusing the user's `CREATE TYPE` would be dropped silently and downstream `value::<type>` casts against the rebuilt DB would fail.
- **Generated columns.** `duckdb_tables().sql` keeps the virtual column in the column list, but `INSERT INTO ... SELECT *` rejects it as a target, so the rebuild crashed opaquely at the per-table insert.
- **Self-referential foreign keys.** `duckdb_tables().sql` drops the FK clause and leaves a trailing comma in the column list. Lenient DuckDB parsers lost the constraint silently; stricter ones crashed at `CREATE TABLE`. Refusing turns either outcome into a clear pre-target error.
- **HNSW indexes on non-bare-column expressions.** `_HNSW_COLUMN_RE` is non-greedy and truncates the captured key at the first inner `)`, so an expression like `CAST(col AS FLOAT[N])` would round-trip as malformed DDL. Refusing keeps the recipe round-trip honest and removes the deferred `restore`-time crash.
- **Foreign-key cycles.** Topological order is undefined for a cycle; deferring constraints is not generally portable across DuckDB versions. `_topological_order` raises `ValueError`.
- **HNSW tuning parameters other than `metric`.** `M`, `M0`, `ef_construction`, `ef_search` are not surfaced by any pragma, so they cannot be recovered from a built index. If you depended on tuned values, recreate those indexes manually after compaction.
- **Table and column comments.** `COMMENT ON` survives `duckdb_tables().sql` only for the table-DDL string, not as a separate catalog entry the rebuild copies. ChunkHound does not use comments (verified against the production index), so this affects no real workload, but the rebuilt file will not carry them.

For approaches that were considered and deliberately not pursued (DiskANN, `PRAGMA hnsw_compact_index`, schema evolution, out-of-core HNSW, resume after partial failure, `--memory-limit` / `--temp-dir` flags), see [out-of-scope.md](out-of-scope.md).
