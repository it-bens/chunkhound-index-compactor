from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from chunkhound_index_compactor import (
    CompactionResult,
    compact_database,
    replace_with_compacted,
)


def test_compact_returns_result(populated_db: Path, tmp_path: Path) -> None:
    # Verify the returned CompactionResult lines up with the actual filesystem
    # state AND that the rebuilt file holds the row count the source had. The
    # size-only check alone passed even on an empty-header DuckDB file.
    target = tmp_path / "out.duckdb"
    result = compact_database(populated_db, target)

    assert result.source == populated_db
    assert result.target == target
    assert result.source_size == populated_db.stat().st_size
    assert result.target_size == target.stat().st_size

    out = duckdb.connect(str(target), read_only=True)
    try:
        row = out.execute("SELECT count(*) FROM items").fetchone()
    finally:
        out.close()
    assert row is not None
    assert row[0] == 100


def test_compact_roundtrips_data(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    compact_database(populated_db, target)

    conn = duckdb.connect(str(target), read_only=True)
    try:
        row = conn.execute("SELECT count(*) FROM items").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == 100


def test_compact_removes_unused_spill_dir(populated_db: Path, tmp_path: Path) -> None:
    # The spill dir is created beside the target up front; when DuckDB never
    # spills (small source) it must not be left behind.
    target = tmp_path / "out.duckdb"
    compact_database(populated_db, target)

    assert not (tmp_path / ".chunkhound-compactor.tmp").exists()


def test_compact_shrinks_bloated_db(bloated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    result = compact_database(bloated_db, target)

    assert result.target_size < result.source_size
    assert result.delta_bytes < 0
    assert result.delta_pct < 0


def test_compact_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="source database not found"):
        compact_database(tmp_path / "missing.duckdb", tmp_path / "out.duckdb")


def test_compact_target_exists_raises(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    target.write_bytes(b"")
    with pytest.raises(FileExistsError, match="target already exists"):
        compact_database(populated_db, target)


def test_compact_same_path_raises(populated_db: Path) -> None:
    with pytest.raises(ValueError, match="differ from source"):
        compact_database(populated_db, populated_db)


def test_replace_creates_backup_and_swaps(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    compact_database(populated_db, target)
    original_bytes = populated_db.read_bytes()

    backup = replace_with_compacted(populated_db, target)

    assert backup == populated_db.with_suffix(populated_db.suffix + ".bak")
    assert backup.is_file()
    assert backup.read_bytes() == original_bytes
    assert populated_db.is_file()
    assert not target.exists()


def test_replace_falls_back_when_second_rename_crosses_devices(
    populated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When the compacted file lives on a different filesystem from the source
    # (e.g. user-supplied --target on another mount), `Path.rename` raises
    # EXDEV. The fallback path must complete the swap via `shutil.move`.
    target = tmp_path / "out.duckdb"
    compact_database(populated_db, target)
    original_bytes = populated_db.read_bytes()

    original_rename = Path.rename
    state = {"calls": 0}

    def selective_rename(self: Path, dst: Path) -> Path:
        state["calls"] += 1
        if state["calls"] == 2:
            raise OSError(18, "Invalid cross-device link")
        return original_rename(self, dst)

    monkeypatch.setattr(Path, "rename", selective_rename)

    backup = replace_with_compacted(populated_db, target)

    assert backup.is_file()
    assert backup.read_bytes() == original_bytes
    assert populated_db.is_file()
    assert not target.exists()


def test_replace_refuses_existing_backup(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    compact_database(populated_db, target)
    existing_backup = populated_db.with_suffix(populated_db.suffix + ".bak")
    existing_backup.write_bytes(b"prior")

    with pytest.raises(FileExistsError, match="backup path already exists"):
        replace_with_compacted(populated_db, target)


def test_replace_missing_source_raises(tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    target.write_bytes(b"x")
    with pytest.raises(FileNotFoundError, match="source not found"):
        replace_with_compacted(tmp_path / "nope.duckdb", target)


def test_replace_missing_compacted_raises(populated_db: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="compacted file not found"):
        replace_with_compacted(populated_db, tmp_path / "nope.duckdb")


def test_delta_pct_zero_on_empty_source() -> None:
    result = CompactionResult(source=Path("a"), target=Path("b"), source_size=0, target_size=0)
    assert result.delta_pct == 0.0
