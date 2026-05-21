# Out of Scope

Source shapes the compactor refuses, metadata the rebuild drops, latent code edges no real workload hits, and approaches considered against the motivating [ChunkHound](https://github.com/chunkhound/chunkhound) workload and not pursued. Each section carries the why-not and, where the gap is mechanical enough to describe, a fix shape so a future maintainer (ChunkHound's schema evolves, a second consumer arrives) starts from an inventory rather than rediscovering each gap from a test failure.

## Non-`main` schemas

Reproducing non-`main` schemas requires emitting `CREATE SCHEMA` before tables and rewriting `REFERENCES` clauses to be schema-qualified. Silently dropping schemas would corrupt the user's data model. `_reject_unsupported_objects` refuses any source object whose `schema_name != 'main'` and raises `ValueError` before `ATTACH dst`.

**Fix shape.**

1. Capture schema DDL from `duckdb_schemas() WHERE NOT internal`, emit `CREATE SCHEMA "<name>"` before any `CREATE TABLE` for that schema.
2. Qualify the INSERT target: `INSERT INTO "<schema>"."<table>" SELECT * FROM src."<schema>"."<table>"`.
3. Rewrite `REFERENCES` clauses so cross-schema FKs resolve. `duckdb_tables().sql` does not currently qualify them.
4. Pass the qualified `<schema>.<table>` key into `_topological_order` so the dependency graph spans schemas.

Regression test: a source with two schemas, the second's table FK-referencing the first's. Assert both round-trip with row content and the FK resolves on the rebuilt file.

## Views

Reproducing views requires resolving them against the rebuilt tables (some DuckDB versions resolve the view's SELECT at CREATE time). `_reject_unsupported_objects` refuses any row in `duckdb_views()`.

**Fix shape.**

Capture step against `duckdb_views().sql` plus a replay phase after the per-table INSERT loop finishes. A dependency walk on the view set is needed for view-on-view stacks: replay leaves first, parents last.

Regression test: a base table, one view over it, one view-on-view. Assert both views appear in the rebuilt catalog and return rows.

## User-defined types

`duckdb_tables().sql` inlines ENUM / STRUCT / alias definitions into the column declaration when emitting `CREATE TABLE`. Without refusing, a verbatim rebuild silently drops the named `CREATE TYPE` and any `value::<type>` cast against the rebuilt DB fails with "type does not exist". `_reject_unsupported_objects` refuses any row in `duckdb_types() WHERE NOT internal`.

**Fix shape.**

1. Add a capture step for `duckdb_types() WHERE NOT internal` returning name and definition.
2. Emit `CREATE TYPE "<name>" AS <definition>` before any `CREATE TABLE` references it.
3. Verify the rebuilt `duckdb_tables().sql` references the type by name rather than re-inlining; if it re-inlines, the captured DDL has to be rewritten column-by-column.

Regression test: a source with one ENUM type and one table column of that type. Assert the rebuilt schema preserves the type by name, not just the underlying representation.

## Generated columns

`duckdb_tables().sql` keeps the virtual column in the column list, but `INSERT INTO ... SELECT *` rejects it as a write target, so the rebuild crashed opaquely at the per-table insert. `_reject_unsupported_objects` refuses any table whose `duckdb_tables().sql` matches `_GENERATED_COLUMN_RE` (`\bGENERATED\s+ALWAYS\b`).

**Fix shape.**

DuckDB 1.5.2's `duckdb_columns()` does not expose a `generated_expression` flag (the expression appears in `column_default`, indistinguishable from a plain DEFAULT), so identifying generated columns needs a parse of `duckdb_tables().sql`:

1. Parse each table's column declarations from `duckdb_tables().sql`; flag any column whose declaration contains `GENERATED ALWAYS`.
2. Build an explicit column list per table (non-generated columns only) and switch the rebuild's INSERT from `SELECT *` to `INSERT INTO {q} (cols) SELECT cols FROM src.{q}`.
3. Generated values rederive from the rebuilt rows automatically; no extra step.

Regression test: a source with a `GENERATED ALWAYS AS (id * 2)` column populated by 10 rows. Assert the rebuilt table has 10 rows and the virtual column re-derives.

## Self-referential foreign keys

A self-referential FK survives `duckdb_tables().sql` as invalid DDL: the FK clause gets dropped and the column list keeps a trailing comma. Lenient DuckDB parsers lose the constraint silently; stricter ones crash at `CREATE TABLE`. Reproducing it would require either a post-create `ALTER TABLE ADD CONSTRAINT` (which DuckDB does not support for FKs) or deferred constraints (not portable across DuckDB versions). `_reject_unsupported_objects` refuses any row in `duckdb_constraints()` where `constraint_type = 'FOREIGN KEY' AND table_name = referenced_table`.

**Fix shape.**

Two viable paths:

1. **Upstream-dependent.** Wait for DuckDB to grow `ALTER TABLE ADD FOREIGN KEY` or portable deferred constraints, then reconstruct the FK from `duckdb_constraints()` after the table is created and populated.
2. **Two-pass populate.** Capture the FK column from `duckdb_constraints()`. INSERT each row with that column set to NULL, then run a single `UPDATE` to fill the back-edges from the source. Requires the column to be nullable in the rebuilt table, which may need a relaxation that the source did not impose.

Regression test: a `node(id, parent)` table with a single root and a depth-3 chain. Assert the rebuilt table preserves every parent edge and rejects an orphaned parent reference.

## Expression HNSW keys

`_HNSW_COLUMN_RE` is non-greedy and truncates the captured key at the first inner `)`, so an expression like `CAST(col AS FLOAT[N])` would round-trip as malformed DDL. The recipe table also stores the column as a single `VARCHAR` with no schema for arbitrary expressions. `_capture_hnsw_recipes` refuses any HNSW key not matching `_BARE_IDENTIFIER_RE` (a bare identifier or a `"`-quoted identifier).

**Fix shape.**

1. Replace `_HNSW_COLUMN_RE` with a parenthesis-balanced parse of the `USING HNSW (...)` body so an expression is captured intact.
2. Widen the recipe table to store the raw expression alongside the bare-column case, or keep the recipe bare-only and accept that the live HNSW DDL is the source of truth for expression keys (no `--skip-hnsw` support for them).

Regression test: an HNSW on `CAST(raw AS FLOAT[4])`. Assert the index round-trips on the full rebuild and `restore` rebuilds it from a `--skip-hnsw` artifact (if option 1 above is taken).

## Foreign-key cycles

Topological order is undefined for a cycle. Deferring constraints is not generally portable across DuckDB versions. `_topological_order` raises `ValueError`. No fix shape: this is a structural property of the source schema, not a code-level gap.

## HNSW tuning beyond `metric`

`M`, `M0`, `ef_construction`, and `ef_search` affect recall and build/query speed. They aren't recovered because:

- **They're not surfaced by the catalog or any pragma.** `duckdb_indexes().sql` strips the entire `WITH (...)` clause. `pragma_hnsw_index_info()` returns `catalog_name`, `schema_name`, `index_name`, `table_name`, `metric`, `dimensions`, `count`, `capacity`, `approx_memory_usage`, `levels`, and `levels_stats`. None of the build-time tuning knobs appear there. There's no way to read them off a built index.
- **They're dominated by upstream factors.** The embedding model and reranking strategy (ChunkHound's MultiHopStrategy) move recall far more than these knobs.
- **Defaults are sane.** vss defaults (`connectivity`/`M`, `expansion_add`/`ef_construction`, `expansion_search`/`ef_search`) work for the ChunkHound workload.

If you depended on tuned values, you have to recreate those indexes manually after compaction. The tool does not pretend it can preserve them.

**Fix shape.**

The gap is upstream: a `pragma_hnsw_index_info()` extension or sibling pragma that reports build-time parameters. Once that lands, closing the gap on this side needs:

1. Read the extra columns alongside `metric` in `_capture_hnsw_recipes`.
2. Pass each through to the `WITH (...)` clause of the rebuilt `CREATE INDEX`.
3. Widen the recipe table with `m`, `m0`, `ef_construction`, `ef_search` columns, all nullable so existing recipes still load on the new code.

## Table and column comments

Comments are stored in the `comment` column of `duckdb_tables()` and `duckdb_columns()`, not in the `.sql` DDL the rebuild captures (verified on DuckDB 1.5.2). The rebuild drops them because ChunkHound does not use comments (verified against the production index), so no real workload is affected. Preserving them would add a per-table and per-column catalog walk plus `COMMENT ON` emission with no current consumer.

**Fix shape.**

1. After the per-table INSERT phase and before the HNSW rebuild, walk both catalog columns.
2. Emit `COMMENT ON TABLE "<t>" IS '<comment>'` and `COMMENT ON COLUMN "<t>"."<c>" IS '<comment>'` for any non-null `comment`. Both already accept SQL-string literals; route through `_escape_sql_literal`.

Regression test: a source with one `COMMENT ON TABLE` and one `COMMENT ON COLUMN`. Assert both round-trip into the rebuilt catalog.

## Quoted referenced tables in `_FK_REFERENCES_RE`

`_FK_REFERENCES_RE = r"FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+(\w+)"` matches only bare identifiers. A DDL like `REFERENCES "tab"(id)` would not be captured, so `_topological_order` could miss the parent-to-child edge and INSERT the child first, tripping the FK at runtime. ChunkHound uses bare identifiers; the front gate refuses many non-ChunkHound shapes but does not refuse this one.

**Fix shape.**

1. Extend the regex: `r'FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+("(?:[^"]|"")+"|\w+)'`.
2. Strip surrounding quotes (and un-double any embedded `""`) in `_referenced_tables` so the captured name matches the unquoted `table_ddls` key.

Regression test: a parent table whose name is a SQL reserved word (DuckDB serializes it quoted) and a child FK-referencing it. Assert the rebuild orders parent before child and the FK resolves.

## DiskANN or alternative ANN backends

ChunkHound writes HNSW via `duckdb-extension-vss`. Switching to DiskANN (or another on-disk ANN structure) would change the index format, break ChunkHound's read path, and require a coordinated change in ChunkHound itself. The motivating workload is "compact what ChunkHound writes," not "replace the ANN."

**Fix shape.**

A coordinated change in ChunkHound on the read side is the load-bearing piece. On the compactor side the implementation shape is straightforward: `_load_bundled_extension` already knows how to load extension binaries from disk, and the recipe table would gain an `index_kind` column so `restore` knows which `CREATE INDEX` syntax to emit. The contract with the consumer (which backend ChunkHound reads from) matters more than the implementation.

## `PRAGMA hnsw_compact_index('<index>')`

vss provides an in-place HNSW compaction pragma that prunes tombstones from the index without rebuilding the whole database. Two problems for this tool's purpose:

1. It still loads the HNSW into RAM (so it doesn't help the small-RAM case `--skip-hnsw` exists for).
2. It doesn't reclaim space outside the HNSW. The ChunkHound bloat shape is orphaned-used blocks from past HNSW serializations, not live-but-tombstoned entries in the current one, so the pragma touches the wrong region.

The pragma is the right tool when an HNSW has accumulated deletes; it isn't the tool when the source database has accumulated orphaned blocks across many HNSW lifecycles.

**Fix shape.**

For a tombstone-heavy workload (high delete volume rather than the orphaned-block shape that motivated the current rebuild), wiring this in is a new CLI verb, not a flag on `compact`. A `compact-indexes` verb that opens the source read-write, `LOAD`s `vss`, and runs `PRAGMA hnsw_compact_index('<index>')` against each HNSW would suffice. RAM profile, locking, and result schema all differ from the rebuild path, so sharing the existing `compact` surface would muddle the contract.

## Out-of-core HNSW build

vss requires the HNSW to fit fully in RAM at both build and query time. There is no streaming-build or mmap-backed path inside vss; the index either fits or doesn't. `--skip-hnsw` exists because that constraint is upstream of this tool. The workaround is to defer the build to a RAM-capable machine, not to invent an out-of-core build path.

**Fix shape.**

The fix lives in vss, not here. If vss grows a streaming-build path, the `restore` command can drop its RAM-capable-machine framing in the README and architecture.md.

## Resume after partial failure

The compaction pipeline has many failure points (source attach, schema reject, FK topo sort, per-table INSERT, index DDL replay, HNSW build, checkpoint). On any failure after the target file is attached, the partial target is unlinked. Resuming would mean carrying enough state to know which step failed and that everything before it was durable on disk; for the target workload (single CLI run, deterministic schema) this overhead doesn't pay back. A half-written multi-GB file is worse than nothing.

**Fix shape.**

For a workload that compacts very-large indexes in a CI pipeline with limited per-step wall time, resume might justify itself. The work:

1. Persisted progress: which step succeeded, which table is currently mid-INSERT, which indexes are built.
2. A way to reattach a partially populated target. `compact_database` today refuses an existing target outright (`FileExistsError`).
3. A resume flag on the CLI that bypasses the existence check and reads the progress file.

## `--memory-limit` and `--temp-dir` flags

DuckDB exposes `SET memory_limit = '<n>GiB'` and `SET temp_directory = '<path>'`. The tool could surface them as flags. It doesn't, because:

- The flags multiply the test matrix without changing any contract.
- DuckDB's own settings can be configured via env or a connection-level override outside the tool.
- The motivating workload either fits the rebuild in RAM or uses `--skip-hnsw`; intermediate spill behavior isn't on the user's critical path.

**Fix shape.**

The `temp_directory` co-location in `compact_database` is fixed at `<target.parent>/.chunkhound-compactor.tmp`. If a user workload starts needing override (managed-mount setup, separate fast scratch disk), the change is small:

1. Add Typer `--temp-dir PATH` and `--memory-limit STRING` options to `compact`.
2. Plumb through `compact_database(source, target, *, skip_hnsw=False, temp_dir=None, memory_limit=None)`.
3. Issue `SET temp_directory = ...` and `SET memory_limit = ...` against the in-memory connection before `ATTACH src`.

The test matrix grows but the contract surface stays small.

## `--strategy copy|rebuild`

Earlier iterations of this tool exposed `COPY FROM DATABASE` as a strategy alongside the rebuild. It's gone:

- `COPY` fails on the motivating workload (the FK race the rebuild path was built to avoid).
- COPY's only structural edge would have been HNSW byte-identity. The rebuilt HNSW reproduces ChunkHound's existing per-batch drift anyway, so there's no determinism win either.

No fix shape: bringing the flag back means re-introducing the FK race the rebuild was built to avoid.

## `restore` as a flag rather than a command

`restore` runs on a different machine than `compact --skip-hnsw`, takes different inputs, and runs later. It is a distinct operation, not a mode of compaction. Surfacing it as `--restore` would couple two operations that production usage already separates. No fix shape: this is a UX decision, not a code-level gap.
