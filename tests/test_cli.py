from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from chunkhound_index_compactor import cli as cli_mod
from chunkhound_index_compactor import core
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


def test_cli_compact_surfaces_missing_vss_extension_cleanly(
    hnsw_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing bundled vss binary surfaces as RuntimeError from
    # _bundled_extension_path. The CLI must turn that into a clean `error:`
    # line, not a stack trace.
    def fake(_ext: str) -> Path:
        raise RuntimeError("bundled vss missing for test")

    monkeypatch.setattr(core, "_bundled_extension_path", fake)

    target = tmp_path / "out.duckdb"
    result = runner.invoke(app, [str(hnsw_db), str(target)])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "bundled vss missing for test" in result.output


def test_cli_restore_surfaces_missing_vss_extension_cleanly(
    hnsw_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build a --skip-hnsw artifact first (bundled vss is needed to read the
    # source's metric), then break vss lookup for the restore call only.
    target = tmp_path / "out.duckdb"
    skip = runner.invoke(app, [str(hnsw_db), str(target), "--skip-hnsw"])
    assert skip.exit_code == 0, skip.output

    def fake(_ext: str) -> Path:
        raise RuntimeError("bundled vss missing for restore test")

    monkeypatch.setattr(core, "_bundled_extension_path", fake)

    result = runner.invoke(app, ["restore", str(target)])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "bundled vss missing for restore test" in result.output


def test_cli_replace_skip_hnsw_note_points_at_source_path(hnsw_db: Path) -> None:
    # After --replace, the .compacted artifact is renamed onto the source
    # path. A note that still references <source>.compacted points the user at
    # a file that no longer exists.
    result = runner.invoke(app, [str(hnsw_db), "--replace", "--skip-hnsw"])
    assert result.exit_code == 0, result.output

    compacted_path = str(hnsw_db) + ".compacted"
    assert f"restore {hnsw_db}" in result.output
    assert f"restore {compacted_path}" not in result.output


def test_cli_replace_skip_hnsw_warns_in_place_db_has_no_vector_index(
    hnsw_db: Path,
) -> None:
    # --replace + --skip-hnsw leaves the in-place file with no vector index
    # until `restore` runs against it. That regression needs to be loud.
    result = runner.invoke(app, [str(hnsw_db), "--replace", "--skip-hnsw"])
    assert result.exit_code == 0, result.output
    assert "warning:" in result.output.lower()
    assert "no vector index" in result.output.lower()


def test_cli_compact_surfaces_spill_dir_failure_cleanly(
    populated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The spill dir is created beside the target before compaction; on a
    # read-only or full filesystem its mkdir raises OSError. The CLI must turn
    # that into a clean `error:` line, not a stack trace.
    original_mkdir = Path.mkdir

    def boom(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == ".chunkhound-compactor.tmp":
            raise PermissionError("read-only filesystem")
        original_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", boom)

    result = runner.invoke(app, [str(populated_db), str(tmp_path / "out.duckdb")])
    assert result.exit_code == 1
    assert "error:" in result.output


def test_cli_replace_handles_os_error_cleanly(
    populated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cross-device or filesystem-level failure in replace_with_compacted
    # surfaces as OSError. The CLI must print a clean `error:` line, not a
    # stack trace.
    def boom(_source: Path, _target: Path) -> Path:
        raise OSError("simulated cross-device replace failure")

    monkeypatch.setattr(cli_mod, "replace_with_compacted", boom)

    result = runner.invoke(app, [str(populated_db), "--replace"])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "simulated cross-device replace failure" in result.output
