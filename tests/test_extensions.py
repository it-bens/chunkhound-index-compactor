from __future__ import annotations

from pathlib import Path

import duckdb
from chunkhound_index_commons.vss import bundled_vss_path

from chunkhound_index_compactor import compact_database


def test_compact_hnsw_db_rebuilds(hnsw_db: Path, tmp_path: Path) -> None:
    # Rebuild must preserve the table contents AND the HNSW index, not just
    # produce a non-empty file. The size-only check passed on a DuckDB file
    # holding nothing but a header.
    target = tmp_path / "out.duckdb"
    result = compact_database(hnsw_db, target)
    assert target.is_file()
    assert result.target_size > 0

    out = duckdb.connect(str(target), read_only=True)
    try:
        out.execute(f"LOAD '{bundled_vss_path()}'")
        row_count = out.execute("SELECT count(*) FROM vectors").fetchone()
        index_count = out.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE sql ILIKE '%USING HNSW%'"
        ).fetchone()
    finally:
        out.close()

    assert row_count is not None
    assert row_count[0] == 50
    assert index_count is not None
    assert index_count[0] == 1


def test_compact_shopware_cli_index_shrinks_substantially(
    shopware_cli_index: Path, tmp_path: Path
) -> None:
    target = tmp_path / "compacted.duckdb"
    result = compact_database(shopware_cli_index, target)

    # Full rebuild (HNSW recreated) must still be ≥30 % smaller than the source.
    assert result.target_size < result.source_size * 0.7, (
        f"insufficient shrink: {result.source_size} -> {result.target_size}"
    )
