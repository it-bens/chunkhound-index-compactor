# Out of Scope

Compactor pipeline-state and CLI-UX decisions considered against the motivating [ChunkHound](https://github.com/chunkhound/chunkhound) workload and not pursued. Refused source shapes, dropped metadata, latent code edges, and rejected substrate approaches are commons-level and live in [commons out-of-scope.md](https://github.com/it-bens/chunkhound-index-commons/blob/main/docs/out-of-scope.md). Each section below carries the why-not and, where the gap is mechanical enough to describe, a fix shape.

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
