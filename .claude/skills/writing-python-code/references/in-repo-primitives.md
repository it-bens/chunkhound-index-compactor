# In-Repo Primitives (reference)

Standard library or DuckDB calls that look right in isolation often violate an invariant the project enforces only through a wrapper.

## Lookup table

Search the repo for the helper before reaching for the stdlib or DuckDB primitive directly.

| Call shape | Reach for | In-repo helper | Reason the wrapper exists |
|---|---|---|---|
| Embed a user-controlled or runtime string into a DuckDB SQL statement | Inline f-string with `'<value>'` | `_escape_sql_literal(value)` in `core.py` | DuckDB DDL does not accept parameter binding. A bare single quote in the value closes the literal and turns the suffix into SQL. Doubling single quotes (the DuckDB literal escape) is the only safe path. |
| `LOAD` a DuckDB extension | Hand-written `INSTALL` + `LOAD` against a network registry | `_load_bundled_extension(conn, "vss")` (calls `_bundled_extension_path("vss")`) | The project is offline-safe by design. The bundled `duckdb-extension-vss` wheel ships the `.duckdb_extension` binary; `INSTALL` would force a network round-trip and lose that guarantee. |
| Locate the `vss` binary on disk | Hard-coded path under the wheel's install location | `_bundled_extension_path(ext)` | The wheel's internal path includes a version-suffixed `v*/` directory that drifts with each `duckdb-extension-vss` release. The helper globs `v*/<ext>.duckdb_extension` and picks the highest. |
| Build a topological order over FK-dependent tables | Sort by `table_name`, or rely on `duckdb_tables()` row order | `_topological_order(table_ddls)` | The catalog gives no FK information directly; the helper parses `FOREIGN KEY (...) REFERENCES <table>` out of each `CREATE TABLE` DDL via `_FK_REFERENCES_RE`, builds the dependency graph, and raises on a cycle. Insertion order is what avoids the `COPY FROM DATABASE` FK race. |
| Format a byte count for user output | `f"{n} bytes"` or manual division | `human_size(n)` in `core.py` | The project's CLI output and `CompactionResult.delta_pct` text use a single formatter so the same `1.5 GiB → 240.0 MiB (-84.0%)` shape appears everywhere. |
| Detect HNSW indexes in `duckdb_indexes().sql` output | Substring search for `HNSW` | `_HNSW_RE` (re-compiled `r"USING\s+HNSW"`, case-insensitive) | `HNSW` may appear in a comment, an identifier, or a stripped form; `USING\s+HNSW` is the catalog-DDL shape and the only one the rebuilder cares about. |
| Parse the indexed column from an HNSW DDL | Custom string slicing | `_HNSW_COLUMN_RE` | The column expression sits inside `USING HNSW(...)` and may contain function calls. The regex captures the parenthesized expression verbatim. |
| Persist HNSW recipe rows | Inline `INSERT` statements | `_write_recipe_table(conn, recipes)` writing to `RECIPE_TABLE` (`_compactor_hnsw_recipe`) | The table name is the cross-tool contract with `restore_indexes`. Hard-coding the literal in two places would let them drift. |

## Decision Test

Before writing the stdlib or DuckDB call, ask:

> Does this call build a SQL literal, load a DuckDB extension, locate a
> bundled binary, order FK-dependent tables, format bytes for output, or
> parse an HNSW catalog DDL — and does `core.py` already wrap the
> primitive for that case?

- yes, helper exists → use the helper
- yes, no helper exists but the case is in scope of an existing helper → extend the helper; don't add a parallel one
- yes, no helper exists and the case is new → flag and propose adding one rather than reaching past the missing wrapper
- no → stdlib / DuckDB call is fine

## What the lookup is not

The table is not exhaustive. It captures the categories where missed routing through the helper would defeat a project-wide invariant (SQL injection through DDL, network round-trip on extension load, drift between writer and reader of the recipe table). New categories appear when a new wrapper lands; extend the table when it does. Treat the table as evidence-based, not definitional: if a call sits clearly outside these categories, the table does not apply.
