# AGENTS.md (chunkhound-index-compactor)

Operational navigation for LLM coding agents. Human docs: `README.md`, `docs/architecture.md`, `docs/benchmarks.md`, `docs/out-of-scope.md`.

## Layout

```
chunkhound-index-compactor/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── CLAUDE.md                     # @AGENTS.md
├── CHANGELOG.md
├── LICENSE
├── docs/
│   ├── architecture.md           # pipeline, RAM asymmetry, recipe table, vss bundling, ChunkHound compat, refused inputs
│   ├── benchmarks.md             # empirical baseline (1.25 TB ChunkHound index + fixture cross-check)
│   └── out-of-scope.md           # rejected approaches (DiskANN, hnsw_compact_index, M/M0/ef_*, ...)
├── src/chunkhound_index_compactor/
│   ├── __init__.py               # public API re-exports
│   ├── __main__.py               # python -m entry
│   ├── cli.py                    # Typer app
│   └── core.py                   # compaction logic
└── tests/
    ├── conftest.py               # fixtures: populated_db, bloated_db, hnsw_db, shopware_cli_index
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
| `core.py` | `compact_database`, `restore_indexes`, `replace_with_compacted`, `human_size`, `CompactionResult`, `RestoreResult` | `_topological_order`, `_referenced_tables`, `_reject_unsupported_objects`, `_capture_hnsw_recipes`, `_write_recipe_table`, `_load_bundled_extension`, `_bundled_extension_path`, `_escape_sql_literal`, `RECIPE_TABLE` constant, regexes `_HNSW_RE`, `_HNSW_COLUMN_RE`, `_FK_REFERENCES_RE` |
| `cli.py` | `app` (Typer), `compact`, `restore` commands; `DefaultCommandGroup` routes bare args to `compact` | (none) |
| `__main__.py` | `app()` invocation | (none) |
| `__init__.py` | re-exports from `core` | (none) |

## When to modify

| Task | File / symbol |
|---|---|
| Rebuild SQL sequence | `core.py` → `compact_database()` |
| FK ordering | `core.py` → `_topological_order()` / `_referenced_tables()` |
| Schema/view rejection | `core.py` → `_reject_unsupported_objects()` |
| HNSW metric recovery / recipe table schema | `core.py` → `_capture_hnsw_recipes()` / `_write_recipe_table()` / `RECIPE_TABLE` |
| Index restore | `core.py` → `restore_indexes()` |
| Atomic replace / backup suffix | `core.py` → `replace_with_compacted()` |
| CLI args / commands / output strings | `cli.py` (`DefaultCommandGroup`, `compact`, `restore`) |
| Byte formatting | `core.py` → `human_size()` |
| New public export | `core.py` + `__init__.py` `__all__` |
| Pipeline narrative, design rationale, refused-input reasoning | `docs/architecture.md` (not here) |
| Empirical baseline / scale numbers | `docs/benchmarks.md` (not here) |
| Rejected approaches (DiskANN, hnsw_compact_index, M/M0/ef_*, etc.) | `docs/out-of-scope.md` (not here) |

## Invariants enforced by code

- HNSW metric must survive rebuild. Catalog DDL strips `WITH (...)`, so the metric is read from `pragma_hnsw_index_info()` in `_capture_hnsw_recipes`. (architecture.md §ChunkHound compatibility)
- SQL DDL is built by string interpolation (no parameter binding); escape literals via `_escape_sql_literal`, wrap table and index names in double quotes. (architecture.md §Compaction pipeline)
- Public-API exceptions (`ValueError`, `FileNotFoundError`, `FileExistsError`) enumerated at README §Library Usage; refused inputs reasoned at architecture.md §Not supported (and why).
- Reading the source never loads its HNSW into RAM; building the destination HNSW dominates peak RAM. `--skip-hnsw` is the small-RAM unlock; `restore` is a separate-machine step. (architecture.md §RAM cost asymmetry)

## Build / verify

- Setup, local-check commands, tooling configs, CI workflow details, and release process at `CONTRIBUTING.md` §Setup, §Local checks, §CI workflows, §Release process.

## Runtime deps

- Authoritative constraints at `pyproject.toml`. Load-bearing context: `duckdb` range matches `chunkhound` to stay file-format-compatible; `duckdb-extension-vss>=1.5.2` pins `duckdb==1.5.2` transitively. Python `>=3.10,<3.14`.
