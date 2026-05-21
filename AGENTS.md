# AGENTS.md

## Layout

```
chunkhound-index-compactor/
├── pyproject.toml
├── package.json                  # prettier dev dep (Node)
├── .prettierrc.json
├── .prettierignore
├── .typos.toml
├── .github/workflows/            # ci.yml, rolling.yml, release.yml
├── README.md
├── AGENTS.md
├── CLAUDE.md                     # @AGENTS.md
├── CONTRIBUTING.md               # dev tooling, CI, release process
├── CHANGELOG.md
├── LICENSE
├── docs/
│   ├── architecture.md           # pipeline, RAM asymmetry, recipe table, vss bundling, ChunkHound compat, refused inputs
│   ├── benchmarks.md             # empirical baseline (1.25 TB ChunkHound index + fixture cross-check)
│   └── out-of-scope.md           # refused shapes, dropped metadata, latent edges, rejected approaches + fix shapes
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
| `core.py` | `compact_database`, `restore_indexes`, `replace_with_compacted`, `human_size`, `CompactionResult`, `RestoreResult` | `_topological_order`, `_referenced_tables`, `_reject_unsupported_objects`, `_capture_hnsw_recipes`, `_write_recipe_table`, `_load_bundled_extension`, `_bundled_extension_path`, `_escape_sql_literal`, `_quote_identifier`, `RECIPE_TABLE` constant, regexes `_HNSW_RE`, `_HNSW_COLUMN_RE`, `_FK_REFERENCES_RE`, `_GENERATED_COLUMN_RE`, `_BARE_IDENTIFIER_RE` |
| `cli.py` | `app` (Typer), `compact`, `restore` commands; `DefaultCommandGroup` routes bare args to `compact` | (none) |
| `__main__.py` | `app()` invocation | (none) |
| `__init__.py` | re-exports from `core` | (none) |

## When to modify

| Task | File / symbol |
|---|---|
| Rebuild SQL sequence | `core.py` → `compact_database()` |
| FK ordering | `core.py` → `_topological_order()` / `_referenced_tables()` |
| Front-gate refusal of unsupported source shapes | `core.py` → `_reject_unsupported_objects()` (schemas, views, user-defined types, generated columns, self-ref FKs) and `_capture_hnsw_recipes()` (expression HNSW columns) |
| Cross-filesystem replace fallback | `core.py` → `replace_with_compacted()` (`shutil.move` on EXDEV) |
| DuckDB spill location | `core.py` → `compact_database()` (architecture.md §Compaction pipeline) |
| HNSW metric recovery / recipe table schema | `core.py` → `_capture_hnsw_recipes()` / `_write_recipe_table()` / `RECIPE_TABLE` |
| Index restore | `core.py` → `restore_indexes()` |
| Atomic replace / backup suffix | `core.py` → `replace_with_compacted()` |
| CLI args / commands / output strings | `cli.py` (`DefaultCommandGroup`, `compact`, `restore`) |
| Byte formatting | `core.py` → `human_size()` |
| New public export | `core.py` + `__init__.py` `__all__` |
| Pipeline narrative, design rationale, refused-input reasoning | `docs/architecture.md` (not here) |
| Empirical baseline / scale numbers | `docs/benchmarks.md` (not here) |
| Refused shapes, dropped metadata, latent edges, rejected approaches, and the fix shape per item | `docs/out-of-scope.md` (not here) |

## Invariants enforced by code

- HNSW metric must survive rebuild. Catalog DDL strips `WITH (...)`, so the metric is read from `pragma_hnsw_index_info()` in `_capture_hnsw_recipes`. (architecture.md §ChunkHound compatibility)
- SQL DDL is built by string interpolation (no parameter binding); escape literals via `_escape_sql_literal`, wrap table and index names via `_quote_identifier`. (architecture.md §Compaction pipeline)
- Public-API exceptions (`ValueError`, `FileNotFoundError`, `FileExistsError`, `RuntimeError`, `OSError`) enumerated at README §Library Usage; refused inputs reasoned at architecture.md §Not supported (and why).
- Front-gate refusals run before `ATTACH dst`; on any failure after `ATTACH dst`, the partial target and its `.wal` are unlinked. (architecture.md §Compaction pipeline)
- Reading the source never loads its HNSW into RAM; building the destination HNSW dominates peak RAM. `--skip-hnsw` is the small-RAM unlock; `restore` is a separate-machine step. (architecture.md §RAM cost asymmetry)

## Build / verify

- Setup, local-check commands, tooling configs, CI workflow details, and release process at `CONTRIBUTING.md` §Setup, §Local checks, §CI workflows, §Release process.

## Runtime deps

- Authoritative constraints at `pyproject.toml`. Load-bearing context: `duckdb` range matches `chunkhound` to stay file-format-compatible; `duckdb-extension-vss>=1.5.2` pins `duckdb==1.5.2` transitively. Python `>=3.10,<3.14`.
