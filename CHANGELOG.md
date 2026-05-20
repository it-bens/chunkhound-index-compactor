# Changelog

## [0.1.1] - 2026-05-20

### Packaging
- README, `[project.urls]` (Homepage / Repository / Issues / Changelog), `authors`, `keywords`, and trove classifiers now ship in the published package metadata. The PyPI project page renders the README and links back to the GitHub repository; 0.1.0 shipped without any of these.

## [0.1.0] - 2026-05-20

### Added
- Initial release of `chunkhound-index-compactor`.
- `chunkhound-index-compactor` CLI (Typer-based) with a `compact` default command and a `restore` subcommand, routed via `DefaultCommandGroup` so `chunkhound-index-compactor SOURCE [TARGET]` still works.
- `compact_database(source, target, *, skip_hnsw=False)`: rebuild a DuckDB database into a fresh file via a foreign-key-ordered streaming rebuild. Captures the source schema, recreates sequences/tables/indexes in a freshly-allocated file, computes a foreign-key-topological table order, and inserts one table at a time parent-before-child. This sidesteps the FK race that breaks `ATTACH` + `COPY FROM DATABASE` on large FK-bearing databases (e.g. ChunkHound indexes at scale) while still dropping orphaned blocks.
- HNSW indexes are recreated with the metric recovered from `pragma_hnsw_index_info()`. The catalog DDL strips the `WITH (...)` clause, so a verbatim rebuild would silently reset a `cosine` index to the `l2sq` default and leave it dead (queries fall back to brute force).
- `--skip-hnsw` flag / `skip_hnsw=True` parameter: rebuild without vector indexes (RAM-flat, smallest output) and record what was stripped in a `_compactor_hnsw_recipe` table inside the output.
- `restore` CLI command / `restore_indexes()` function: rebuild the stripped HNSW indexes in place from the recipe table, idempotently, on a RAM-capable machine.
- `replace_with_compacted()`: atomic swap with `.bak` backup.
- `human_size()`: binary-prefix byte formatting.
- `CompactionResult` and `RestoreResult` dataclasses.
- `--replace` flag for in-place compaction with backup.
- Bundled `vss.duckdb_extension` binary from `duckdb-extension-vss` is `LOAD`ed directly from disk when an HNSW index is present, so compaction of ChunkHound and other vector-search DuckDBs works offline out of the box.

### Fail-hard
- Sources with non-`main` schemas, views, or foreign-key cycles raise `ValueError` rather than silently dropping objects.
- On any failure after the target file is created, the partial target is unlinked. A half-written multi-GB file is worse than nothing.
