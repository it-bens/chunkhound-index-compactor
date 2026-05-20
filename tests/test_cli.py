from __future__ import annotations

from pathlib import Path

import duckdb
from typer.testing import CliRunner

from chunkhound_index_compactor.cli import app
from chunkhound_index_compactor.core import _bundled_extension_path

runner = CliRunner()


def _has_hnsw(database: Path) -> bool:
    conn = duckdb.connect(str(database), read_only=True)
    try:
        conn.execute(f"LOAD '{_bundled_extension_path('vss')}'")
        rows = conn.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE sql ILIKE '%USING HNSW%'"
        ).fetchone()
    finally:
        conn.close()
    return bool(rows and rows[0])


def test_cli_default_target(populated_db: Path) -> None:
    result = runner.invoke(app, [str(populated_db)])
    assert result.exit_code == 0, result.output
    expected = populated_db.with_suffix(populated_db.suffix + ".compacted")
    assert expected.is_file()


def test_cli_explicit_target(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "explicit.duckdb"
    result = runner.invoke(app, [str(populated_db), str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_file()


def test_cli_replace_swaps_and_backs_up(populated_db: Path) -> None:
    original_bytes = populated_db.read_bytes()
    result = runner.invoke(app, [str(populated_db), "--replace"])
    assert result.exit_code == 0, result.output

    backup = populated_db.with_suffix(populated_db.suffix + ".bak")
    assert backup.is_file()
    assert backup.read_bytes() == original_bytes
    assert populated_db.is_file()


def test_cli_replace_refuses_existing_backup_exits_nonzero(populated_db: Path) -> None:
    backup = populated_db.with_suffix(populated_db.suffix + ".bak")
    backup.write_bytes(b"prior")

    result = runner.invoke(app, [str(populated_db), "--replace"])

    assert result.exit_code == 1
    assert "backup path already exists" in result.output


def test_cli_missing_source_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(app, [str(tmp_path / "missing.duckdb")])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_cli_target_exists_exits_nonzero(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    target.write_bytes(b"")
    result = runner.invoke(app, [str(populated_db), str(target)])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_cli_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "compact" in result.output
    assert "restore" in result.output


def test_cli_restore_help_lists_database() -> None:
    result = runner.invoke(app, ["restore", "--help"])
    assert result.exit_code == 0
    assert "DATABASE" in result.output


def test_cli_skip_hnsw_drops_index(hnsw_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    result = runner.invoke(app, [str(hnsw_db), str(target), "--skip-hnsw"])
    assert result.exit_code == 0, result.output
    assert target.is_file()
    assert not _has_hnsw(target)


def test_cli_restore_rebuilds_hnsw(hnsw_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    skip = runner.invoke(app, [str(hnsw_db), str(target), "--skip-hnsw"])
    assert skip.exit_code == 0, skip.output
    assert not _has_hnsw(target)

    restore = runner.invoke(app, ["restore", str(target)])
    assert restore.exit_code == 0, restore.output
    assert _has_hnsw(target)


def test_cli_restore_noop_when_indexes_already_present(hnsw_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    runner.invoke(app, [str(hnsw_db), str(target), "--skip-hnsw"])
    runner.invoke(app, ["restore", str(target)])

    result = runner.invoke(app, ["restore", str(target)])

    assert result.exit_code == 0, result.output
    assert "no-op" in result.output


def test_cli_restore_without_recipe_exits_nonzero(populated_db: Path) -> None:
    result = runner.invoke(app, ["restore", str(populated_db)])
    assert result.exit_code == 1
    assert "recipe" in result.output
