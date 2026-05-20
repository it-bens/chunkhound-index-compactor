from __future__ import annotations

from pathlib import Path

import pytest

from chunkhound_index_compactor import compact_database
from chunkhound_index_compactor.core import _bundled_extension_path


def test_bundled_vss_binary_exists() -> None:
    path = _bundled_extension_path("vss")
    assert path.is_file()
    assert path.name == "vss.duckdb_extension"


def test_bundled_extension_path_unknown_raises() -> None:
    with pytest.raises(RuntimeError, match="no bundled extension known"):
        _bundled_extension_path("definitely-not-a-real-ext")


def test_bundled_extension_path_missing_binary_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb_extension_vss

    # Point the package at an empty dir so the extensions/v*/ glob finds nothing.
    monkeypatch.setattr(duckdb_extension_vss, "__file__", str(tmp_path / "__init__.py"))
    with pytest.raises(RuntimeError, match="no bundled vss.duckdb_extension found"):
        _bundled_extension_path("vss")


def test_compact_hnsw_db_rebuilds(hnsw_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    result = compact_database(hnsw_db, target)
    assert target.is_file()
    assert result.target_size > 0


def test_compact_shopware_cli_index_shrinks_substantially(
    shopware_cli_index: Path, tmp_path: Path
) -> None:
    target = tmp_path / "compacted.duckdb"
    result = compact_database(shopware_cli_index, target)

    # Full rebuild (HNSW recreated) must still be ≥30 % smaller than the source.
    assert result.target_size < result.source_size * 0.7, (
        f"insufficient shrink: {result.source_size} -> {result.target_size}"
    )
