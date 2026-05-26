# AGENTS.md

## Layout

```
chunkhound-index-compactor/
├── pyproject.toml
├── action.yml                    # composite GitHub Action (wraps compact)
├── package.json                  # prettier dev dep (Node)
├── .prettierrc.json
├── .prettierignore
├── .typos.toml
├── .github/workflows/            # ci.yml, rolling.yml, release.yml, test-action.yml
├── scripts/run-action.sh         # action runner: venv + install + invoke CLI
├── README.md
├── AGENTS.md
├── CLAUDE.md                     # @AGENTS.md
├── CONTRIBUTING.md               # dev tooling, CI, release process
├── CHANGELOG.md
├── LICENSE
├── docs/
│   ├── architecture.md           # pipeline, RAM asymmetry, recipe table, refused-input enumeration (substrate rationale in commons)
│   ├── benchmarks.md             # empirical baseline (1.25 TB ChunkHound index + fixture cross-check)
│   └── out-of-scope.md           # compactor pipeline-state + CLI-UX decisions (substrate catalog in commons)
├── src/chunkhound_index_compactor/
│   ├── __init__.py               # public API re-exports
│   ├── __main__.py               # python -m entry
│   ├── cli.py                    # Typer app
│   └── core.py                   # compaction logic
└── tests/
    ├── conftest.py               # fixtures: populated_db, bloated_db, hnsw_db, cosine_hnsw_db, shopware_cli_index
    ├── fixtures/                 # committed real-world DB artifacts (provenance in conftest.py)
    ├── test_core.py
    ├── test_cli.py
    ├── test_extensions.py
    ├── test_rebuild.py
    └── test_human_size.py
```

## Module → symbols

| Module | Public | Private |
|---|---|---|
| `core.py` | `compact_database`, `restore_indexes`, `replace_with_compacted`, `human_size`, `CompactionResult`, `RestoreResult` | `_capture_hnsw_recipes`, `_write_recipe_table`, `RECIPE_TABLE` constant |
| `cli.py` | `app` (Typer), `compact`, `restore` commands; `DefaultCommandGroup` routes bare args to `compact` | (none) |
| `__main__.py` | `app()` invocation | (none) |
| `__init__.py` | re-exports from `core` | (none) |

Resolution, schema inspection, SQL-interpolation safety, and vss/HNSW primitives are imported from `chunkhound_index_commons` (`resolve`, `schema`, `sql`, `vss`) — `resolve_chunkhound_source`, `reject_unsupported_objects`, `topological_order`, `escape_sql_literal`, `quote_identifier`, `is_bare_identifier`, `is_hnsw_index_ddl`, `parse_hnsw_column`, `load_bundled_vss`, `capture_hnsw_metrics`, `recreate_hnsw_index`. They are not defined in `core.py`.

## When to modify

| Task | File / symbol |
|---|---|
| Rebuild SQL sequence | `core.py` → `compact_database()` |
| Shared DuckDB/ChunkHound primitives (resolve, schema, sql, vss) | `chunkhound-index-commons` package (separate repo) |
| FK ordering | `chunkhound_index_commons.schema` → `topological_order()` / `referenced_tables()` |
| Front-gate refusal of unsupported source shapes | `chunkhound_index_commons.schema.reject_unsupported_objects()` (schemas, views, user-defined types, generated columns, self-ref FKs); expression-HNSW refusal in `core.py` → `_capture_hnsw_recipes()` via `commons.sql.is_bare_identifier` |
| Cross-filesystem replace fallback | `core.py` → `replace_with_compacted()` (`shutil.move` on EXDEV) |
| DuckDB spill location | `core.py` → `compact_database()` (architecture.md §Compaction pipeline) |
| HNSW metric recovery / recipe table schema | metric via `commons.vss.capture_hnsw_metrics`; recipe shape in `core.py` → `_capture_hnsw_recipes()` / `_write_recipe_table()` / `RECIPE_TABLE` |
| Index restore | `core.py` → `restore_indexes()` |
| Directory source resolution (point at a ChunkHound index dir) | `chunkhound_index_commons.resolve.resolve_chunkhound_source()`, called from `cli.py` (architecture.md §ChunkHound compatibility) |
| Atomic replace / backup suffix | `core.py` → `replace_with_compacted()` |
| CLI args / commands / output strings | `cli.py` (`DefaultCommandGroup`, `compact`, `restore`) |
| GitHub Action inputs / branding | `action.yml` (README §GitHub Action) |
| Action runner: venv provision, install, CLI invoke | `scripts/run-action.sh` |
| Byte formatting | `core.py` → `human_size()` |
| New public export | `core.py` + `__init__.py` `__all__` |
| Pipeline narrative, design rationale, refused-input reasoning | `docs/architecture.md` (not here) |
| Empirical baseline / scale numbers | `docs/benchmarks.md` (not here) |
| Refused shapes, dropped metadata, latent edges, rejected approaches, and the fix shape per item | `docs/out-of-scope.md` (not here) |

## Invariants enforced by code

- HNSW metric must survive rebuild. Catalog DDL strips `WITH (...)`, so the metric is read from `pragma_hnsw_index_info()` via `commons.vss.capture_hnsw_metrics`, composed in `_capture_hnsw_recipes`. (architecture.md §ChunkHound compatibility)
- SQL DDL is built by string interpolation (no parameter binding); escape literals via `commons.sql.escape_sql_literal`, wrap table and index names via `commons.sql.quote_identifier`. (architecture.md §Compaction pipeline)
- Public-API exceptions (`ValueError`, `FileNotFoundError`, `FileExistsError`, `RuntimeError`, `OSError`) enumerated at README §Library Usage; refused inputs reasoned at architecture.md §Not supported (and why).
- Front-gate refusals run before `ATTACH dst`; on any failure after `ATTACH dst`, the partial target and its `.wal` are unlinked. (architecture.md §Compaction pipeline)
- Reading the source never loads its HNSW into RAM; building the destination HNSW dominates peak RAM. `--skip-hnsw` is the small-RAM unlock; `restore` is a separate-machine step. (architecture.md §RAM cost asymmetry)

## Build / verify

- Setup, local-check commands, tooling configs, CI workflow details, and release process at `CONTRIBUTING.md` §Setup, §Local checks, §CI workflows, §Release process.

## Runtime deps

- Authoritative constraints at `pyproject.toml`. Load-bearing context: the `duckdb` / `duckdb-extension-vss` floors are owned by `chunkhound-index-commons` and inherited transitively (the compactor must not redeclare them); the compactor declares `chunkhound-index-commons>=0.1,<0.2`, `click`, `typer`. Python `>=3.10,<3.15`.
