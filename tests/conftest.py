from __future__ import annotations

from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """A small DuckDB file with one table of 100 rows."""
    db_path = tmp_path / "source.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE items AS SELECT range AS id, 'name-' || range AS name FROM range(100)"
        )
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    return db_path


@pytest.fixture
def bloated_db(tmp_path: Path) -> Path:
    """A DuckDB file with a dropped large table — has reclaimable space."""
    db_path = tmp_path / "bloated.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE big AS SELECT range AS i, repeat('x', 500) AS s FROM range(500000)"
        )
        conn.execute("CHECKPOINT")
        conn.execute("DROP TABLE big")
        conn.execute("CREATE TABLE small AS SELECT range AS i FROM range(10)")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    return db_path


@pytest.fixture
def hnsw_db(tmp_path: Path) -> Path:
    """A DuckDB file with an HNSW (vss) index — requires vss to compact."""
    from chunkhound_index_compactor.core import _bundled_extension_path

    vss_path = _bundled_extension_path("vss")
    db_path = tmp_path / "hnsw.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(f"LOAD '{vss_path}'")
        conn.execute("SET hnsw_enable_experimental_persistence = true")
        conn.execute("CREATE TABLE vectors (id INTEGER, embedding FLOAT[4])")
        conn.execute(
            "INSERT INTO vectors SELECT range, [random(), random(), random(), random()]::FLOAT[4] "
            "FROM range(50)"
        )
        conn.execute("CREATE INDEX vec_idx ON vectors USING HNSW (embedding)")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    return db_path


FIXTURE_DIR = Path(__file__).parent / "fixtures"
SHOPWARE_CLI_INDEX = FIXTURE_DIR / "shopware-cli-chunks.duckdb"


@pytest.fixture
def shopware_cli_index() -> Path:
    """ChunkHound index of github.com/shopware/shopware-cli (~204 MiB).

    Committed under tests/fixtures/. Regenerate with `chunkhound index` in a
    fresh shopware-cli checkout and copy the resulting `chunks.db` over.
    """
    return SHOPWARE_CLI_INDEX
