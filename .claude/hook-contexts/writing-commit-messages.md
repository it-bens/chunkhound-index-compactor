## Pre-Step-8

A change to the `duckdb` or `duckdb-extension-vss` version constraint in `pyproject.toml` (or the resolved version in `uv.lock`) can shift on-disk DuckDB file-format compatibility with ChunkHound. When the supported format range moves, mark the commit breaking (`!`) and state the format impact in the body. A bump that stays within the same on-disk format is `build` without `!`.

## Pre-Step-9

Override the universal scope default with the rules below. Apply in priority order; fall back to the universal default only if no rule matches.

Module scopes (changes confined to one source module):

- `src/chunkhound_index_compactor/core.py` → `core`
- `src/chunkhound_index_compactor/cli.py` → `cli`
- `src/chunkhound_index_compactor/__main__.py` → `cli`
- `src/chunkhound_index_compactor/__init__.py` → scope of the module whose public surface changed (`core` for re-export changes); omit when only packaging metadata moved.

Other scopes:

- Only files under `tests/` (including `tests/fixtures/`) → `tests`.
- Only files under `docs/` → omit scope (type is `docs`).
- Only root packaging files (`pyproject.toml`, `uv.lock`) → omit scope (type is `build`).
- Only agent/tooling docs (`AGENTS.md`, `CLAUDE.md`) or `.claude/` config → omit scope.

Scope omission (overrides the above):

- Changes spanning both `core` and `cli` with no dominant module → omit scope.
- Repository-wide or cross-cutting changes → omit scope.

Confidence handling:

- HIGH: all changed source files map to one module scope.
- LOW: source changes span `core` and `cli` evenly → use `AskUserQuestion` to confirm scope or omission.
