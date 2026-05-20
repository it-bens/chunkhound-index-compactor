# Benchmarks

The numbers here ground the design claims in [architecture.md](architecture.md). They come from a single-machine experimental pass against a real [ChunkHound](https://github.com/chunkhound/chunkhound) index plus the committed fixture in this repo. A single snapshot, not a performance promise: read it as "what to expect at the order of magnitude", not "your run will produce these exact figures."

## Real ChunkHound index, 1.25 TB on disk

A ChunkHound index of [`shopware/shopware`](https://github.com/shopware/shopware) with its Composer dependencies installed (and indexed alongside the application code). 419 619 embeddings at dimension 1024, cosine metric. Single foreign-key chain (`chunks.file_id` referencing `files.id`). Live data (vectors plus chunk text) was approximately 2 GiB; the rest of the file was orphaned-used blocks from ChunkHound's drop-and-recreate-per-batch HNSW serialization. See [architecture.md §Why a custom rebuild](architecture.md#why-a-custom-rebuild-instead-of-copy-from-database) for what gets reclaimed and why.

| Path                                                  | Output size      | Wall time                                           | Peak RAM                     |
|-------------------------------------------------------|------------------|-----------------------------------------------------|------------------------------|
| `ATTACH` + `COPY FROM DATABASE` (the broken baseline) | (none)           | (none)                                              | fails (FK race)              |
| Full rebuild (HNSW recreated, cosine preserved)       | 4.81 GiB         | 75 s                                                | 11.8 GiB                     |
| `--skip-hnsw` rebuild (no vector index)               | 3.15 GiB         | 14 s                                                | 4.0 GiB                      |
| `restore` after `--skip-hnsw` (rebuild HNSW only)     | (no size change) | similar to "Full rebuild minus the streaming phase" | similar to full-rebuild peak |

## What the numbers say

- **`COPY FROM DATABASE` is not a fallback.** It commits child rows in parallel before their FK parents and aborts on the FK race. Failing keys differed between runs on the same source; the data was referentially clean (no orphan rows in `chunks` referencing missing `files`). The rebuild path inserts one table at a time in FK-topological order, which sidesteps the race entirely.
- **Full rebuild peaks at roughly 3× the source HNSW size.** The HNSW for this source was 2.12 GiB in RAM per `pragma_hnsw_index_info().approx_memory_usage`; rebuild peaked at 11.8 GiB. The destination HNSW build dominates; the streaming-copy phase is RAM-flat.
- **`--skip-hnsw` is the small-RAM unlock.** Peak stays around 4 GiB regardless of source HNSW size because no HNSW gets built. Only the recipe table is written. Output size drops by the HNSW footprint.
- **Output size on a clean source can be marginally larger than `COPY`.** The fixture cross-check (below) showed source 203.5 MiB then COPY 50.0 MiB then full rebuild 50.5 MiB then `--skip-hnsw` 31.5 MiB. Tombstone reclamation only pays on a churned source; the bloat-reclamation lever on a clean source is dropping the index, not the rebuild itself.

## Fixture cross-check, 203.5 MiB ChunkHound index

`tests/fixtures/shopware-cli-chunks.duckdb`: ChunkHound index of [`shopware/shopware-cli`](https://github.com/shopware/shopware-cli). 4 878 embeddings at dimension 1024, cosine. Small enough that `COPY FROM DATABASE` accidentally succeeds on it (the FK race manifests only at scale). Reproducible by running this repo's tests.

| Path                          | Output size |
|-------------------------------|-------------|
| Source                        | 203.5 MiB   |
| `COPY FROM DATABASE`          | 50.0 MiB    |
| Full rebuild (HNSW recreated) | 50.5 MiB    |
| `--skip-hnsw` rebuild         | 31.5 MiB    |

The "at least 30% smaller than source" check in `test_compact_shopware_cli_index_shrinks_substantially` is the regression guard for this fixture.

## How to read these numbers

The 1.25 TB baseline is one machine, one workload. Your numbers will differ. The shape that generalizes:

- The streaming-copy phase scales with live data size, not source file size. A 1.25 TB source with 2 GiB of live data streams in seconds.
- The HNSW-rebuild phase scales with HNSW size and dominates peak RAM. Rule of thumb for sizing the machine: 3 to 4 times the source HNSW's `pragma_hnsw_index_info().approx_memory_usage`.
- If you can fit "live data plus a few GiB of overhead" in RAM, `--skip-hnsw` will succeed regardless of how large the source file is. `restore` then runs on a larger machine.
