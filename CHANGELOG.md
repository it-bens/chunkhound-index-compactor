# Changelog

## [Unreleased]

### Added

- Add a GitHub Action that runs `compact` in a workflow without requiring uv or a Python toolchain in the calling job. The action provisions a venv from the runner's preinstalled Python and accepts the same `index-path`, `target`, `replace`, and `skip-hnsw` arguments as the CLI.
- Add Python 3.14 support. The `requires-python` ceiling moves to `<3.15`, and CI runs the test suite on 3.14.

## [0.4.0] - 2026-05-23

### Added

- Top-level `--version` flag prints the installed package version and exits.

## [0.3.0] - 2026-05-23

### Added

- `compact` now accepts a ChunkHound index directory as `SOURCE`. The directory resolves to its database file when exactly one `*.root.json`-paired DuckDB file is present, identified by the DuckDB header magic (`DUCK` at byte 8). Any other directory shape fails and lists the DuckDB files it found so you can pass the correct path.

### Fixed

- `click` is now declared as an explicit runtime dependency. `cli.DefaultCommandGroup` uses `click.Group`, `click.Context`, and `click.Command` directly but only resolved them transitively through `typer`, so a future `typer` dependency change could have silently broken the CLI import surface.
- `_bundled_extension_path` raises a clear `RuntimeError` when `duckdb_extension_vss.__file__` is `None` (namespace packages, frozen imports) instead of an opaque `TypeError`.

## [0.2.0] - 2026-05-21

### Fail-hard

- `compact_database` now refuses sources with user-defined types, generated columns, self-referential foreign keys, or HNSW indexes on non-bare-column expressions, raising `ValueError`. See architecture.md §Not supported (and why).
- `replace_with_compacted` documents `OSError` as a raised exception (the move from `compacted` to `source` failing even via the `shutil.move` fallback).
- `compact_database` and `restore_indexes` document `RuntimeError` for the case where the bundled `vss` extension binary cannot be located.

### Fixed

- The CLI surfaces `RuntimeError` from missing bundled `vss` and `OSError` from `replace_with_compacted` as clean `error:` lines instead of unhandled stack traces.
- The `--skip-hnsw` note now prints after the `--replace` step and points at the path the artifact lives at after the whole CLI run (`result.source` with `--replace`, `result.target` without). Previously it pointed at the `.compacted` path that `--replace` renamed away.
- `replace_with_compacted` falls back to `shutil.move` when the second rename fails with a cross-device error (EXDEV), so a cross-filesystem `--replace` (user-supplied target on another mount) now completes instead of crashing.
- The failure-cleanup block in `compact_database` now also unlinks `<target>.wal` if a CHECKPOINT step left one behind.
- The compact CLI surfaces spill-directory creation failures (`OSError`) as a clean `error:` line, and `compact_database` removes the spill directory when DuckDB never spills into it.
- DDL identifier interpolation routes through a new `_quote_identifier()` helper that doubles any embedded `"`. The previous sites double-quoted identifiers without escaping.

### Added

- `--replace` with `--skip-hnsw` now prints a `warning:` line. The in-place file has no vector index until `restore` runs against it; the regression needs to be loud.

### Changed

- `compact_database` co-locates DuckDB spill with the target's filesystem (`temp_directory` beside the target). See architecture.md §Compaction pipeline.
- README narrowed the "fully generic / works on any single-schema DuckDB file" claim to ChunkHound-shaped inputs; other shapes are refused at the front gate rather than rebuilt with silent loss. The published package description (`pyproject.toml`) dropped its "(ChunkHound index or otherwise)" parenthetical to match.
- `docs/architecture.md` corrects the imprecise "drops HNSW on each write batch" claim to ChunkHound's actual `insert_embeddings_batch` 50-row threshold, removes the wrong COMMENT ON claim (see out-of-scope.md §Table and column comments for the actual drop behavior), and cites [duckdb/duckdb#16785](https://github.com/duckdb/duckdb/issues/16785) for the `COPY FROM DATABASE` FK race.
- `docs/architecture.md` §Not supported collapsed to one-line pointers at `out-of-scope.md`, so per-case refusal reasoning lives on a single surface.
- `docs/out-of-scope.md` promoted to the single per-topic catalog covering refused source shapes, silently-dropped metadata (HNSW tuning beyond `metric`, table and column comments), latent code edges (quoted referenced tables in `_FK_REFERENCES_RE`), and rejected alternative approaches. Each section owns both the why-not and the fix shape.

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
