# Changelog

## [Unreleased]

### Fail-hard

- `compact_database` now refuses sources with user-defined types, generated columns, self-referential foreign keys, or HNSW indexes on non-bare-column expressions. Each refusal raises `ValueError` before `ATTACH dst`, so the target file is never created. Refusals fire in `_reject_unsupported_objects` and `_capture_hnsw_recipes`.
- `replace_with_compacted` documents `OSError` as a raised exception (the move from `compacted` to `source` failing even via the `shutil.move` fallback).
- `compact_database` and `restore_indexes` document `RuntimeError` for the case where the bundled `vss` extension binary cannot be located.

### Fixed

- The CLI surfaces `RuntimeError` from missing bundled `vss` and `OSError` from `replace_with_compacted` as clean `error:` lines instead of unhandled stack traces.
- The `--skip-hnsw` note now prints after the `--replace` step and points at the path the artifact lives at after the whole CLI run (`result.source` with `--replace`, `result.target` without). Previously it pointed at the `.compacted` path that `--replace` renamed away.
- `replace_with_compacted` falls back to `shutil.move` when the second rename raises `OSError`, so a cross-filesystem `--replace` (user-supplied target on another mount) now completes instead of crashing with EXDEV.
- The failure-cleanup block in `compact_database` now also unlinks `<target>.wal` if a CHECKPOINT step left one behind.
- DDL identifier interpolation routes through a new `_quote_identifier()` helper that doubles any embedded `"`. The previous sites double-quoted identifiers without escaping.

### Added

- `--replace` with `--skip-hnsw` now prints a `warning:` line. The in-place file has no vector index until `restore` runs against it; the regression needs to be loud.

### Changed

- `compact_database` sets DuckDB's `temp_directory` to `<target.parent>/.chunkhound-compactor.tmp` so spill stays on the destination filesystem. The default is the CWD-relative `.tmp`, which can fill a small working FS while the destination disk has room.
- README narrowed the "fully generic / works on any single-schema DuckDB file" claim to ChunkHound-shaped inputs; other shapes are refused at the front gate rather than rebuilt with silent loss.
- `docs/architecture.md` corrects the imprecise "drops HNSW on each write batch" claim to ChunkHound's actual `insert_embeddings_batch` 50-row threshold and cites [duckdb/duckdb#16785](https://github.com/duckdb/duckdb/issues/16785) for the `COPY FROM DATABASE` FK race.
- `docs/out-of-scope.md` corrects the `pragma_hnsw_index_info()` column listing and documents why expression HNSW keys and self-referential FKs are out of scope.

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
