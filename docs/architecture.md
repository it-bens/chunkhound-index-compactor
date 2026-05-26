# Architecture

## Why a custom rebuild instead of `COPY FROM DATABASE`

Why `COPY FROM DATABASE` loses to a table-by-table rebuild in foreign-key-topological order lives in [commons architecture.md §Why these primitives](https://github.com/it-bens/chunkhound-index-commons/blob/main/docs/architecture.md#why-these-primitives). The compactor composes that primitive; this section covers only what the rebuild reclaims and why a ChunkHound index bloats in the first place.

What gets reclaimed is not free space. `pragma_database_size` on a real ChunkHound index reports almost no free blocks: the motivating workload showed 4 769 560 used vs 173 free on a 1.1 TiB file (see [benchmarks.md](benchmarks.md)). The bloat is *orphaned used blocks*: old HNSW serializations left behind by ChunkHound's drop-and-recreate index churn on write batches that meet its size threshold (50 rows by default). The `vss` extension re-serializes the whole HNSW on every CHECKPOINT, so each such batch leaves the prior serialization orphaned. The catalog counts them as used because no live object references them; only a fresh-file rewrite drops them, because the rewrite copies live catalog objects only.

This churn is not a one-off. Every incremental index run that crosses the batch threshold orphans another HNSW serialization, so a compacted file re-bloats as ChunkHound indexes new code.

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

SQL literals are built by string interpolation because DuckDB DDL does not accept parameter binding. Single quotes are doubled via `commons.sql.escape_sql_literal`; table and index names are wrapped via `commons.sql.quote_identifier`, which doubles any embedded `"`.

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
| `column_name` | Indexed column (bare identifier)                 |
| `metric`      | Distance metric recovered at compaction time     |

`restore_indexes(database)` opens the file read-write, fails hard if `_compactor_hnsw_recipe` is absent (the file is not a `--skip-hnsw` artifact), `LOAD`s `vss`, and recreates each recipe index that does not already exist (idempotent) with its recorded metric. It returns `RestoreResult(database, restored)` where `restored` is the tuple of index names created this run.

Above some index size, `restore` is required for acceptable query latency; for moderate indexes the brute-force fallback is fine.

The table name is exposed as the `RECIPE_TABLE` constant in `core.py`. ChunkHound ignores the extra table.

## Bundled `vss` extension

The `vss` bundling, the disk-path load that keeps the tool offline-safe, and the metric-only recovery rationale live in [commons architecture.md §Bundled vss extension](https://github.com/it-bens/chunkhound-index-commons/blob/main/docs/architecture.md#bundled-vss-extension) and [§HNSW metric recovery](https://github.com/it-bens/chunkhound-index-commons/blob/main/docs/architecture.md#hnsw-metric-recovery). The pipeline loads it via `commons.vss.load_bundled_vss`, only when the source contains an HNSW index.

## ChunkHound compatibility

The four ChunkHound behaviors that shape the design — cosine-metric writes, write-path-only HNSW rebuilds, per-batch HNSW non-determinism, and the directory-plus-`root.json` layout — live in [commons architecture.md §ChunkHound compatibility](https://github.com/it-bens/chunkhound-index-commons/blob/main/docs/architecture.md#chunkhound-compatibility). Two are load-bearing for the compactor's own surface: `restore` is a separate command because ChunkHound never lazy-rebuilds a stripped index (see §The `_compactor_hnsw_recipe` table), and `compact` accepts a ChunkHound directory because `commons.resolve.resolve_chunkhound_source` resolves it to the inner DuckDB file.

## Not supported (and why)

These cases the rebuild refuses with `ValueError` before `ATTACH dst`, so the target file is never created. All seven are commons-enforced (`reject_unsupported_objects`, `topological_order`, and `parse_hnsw_column` paired with `is_bare_identifier`); per-case reasoning and fix shapes live in [commons out-of-scope.md](https://github.com/it-bens/chunkhound-index-commons/blob/main/docs/out-of-scope.md).

- Non-`main` schemas.
- Views.
- User-defined types.
- Generated columns.
- Self-referential foreign keys.
- Expression HNSW keys.
- Foreign-key cycles.

For metadata the rebuild drops, latent code edges, and rejected approaches — compactor-specific in [out-of-scope.md](out-of-scope.md), substrate-level in [commons out-of-scope.md](https://github.com/it-bens/chunkhound-index-commons/blob/main/docs/out-of-scope.md).
