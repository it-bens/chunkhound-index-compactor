from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from chunkhound_index_compactor import compact_database, restore_indexes
from chunkhound_index_compactor.core import (
    _bundled_extension_path,
    _capture_hnsw_recipes,
    _topological_order,
)


def _hnsw_index_names(database: Path) -> list[str]:
    conn = duckdb.connect(str(database), read_only=True)
    try:
        conn.execute(f"LOAD '{_bundled_extension_path('vss')}'")
        rows = conn.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE sql ILIKE '%USING HNSW%'"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def test_topological_order_sorts_parent_before_child() -> None:
    # Child table sorts alphabetically before its parent.
    ddls = {
        "cats": (
            "CREATE TABLE cats(id INTEGER PRIMARY KEY, owner_id INTEGER, "
            "FOREIGN KEY (owner_id) REFERENCES owners(id))"
        ),
        "owners": "CREATE TABLE owners(id INTEGER PRIMARY KEY)",
    }
    order = _topological_order(ddls)
    assert order.index("owners") < order.index("cats")


def test_topological_order_keeps_all_independent_tables() -> None:
    ddls = {
        "a": "CREATE TABLE a(id INTEGER)",
        "b": "CREATE TABLE b(id INTEGER)",
        "c": "CREATE TABLE c(id INTEGER)",
    }
    assert sorted(_topological_order(ddls)) == ["a", "b", "c"]


def test_topological_order_rejects_cycle() -> None:
    ddls = {
        "x": "CREATE TABLE x(id INTEGER, y_id INTEGER, FOREIGN KEY (y_id) REFERENCES y(id))",
        "y": "CREATE TABLE y(id INTEGER, x_id INTEGER, FOREIGN KEY (x_id) REFERENCES x(id))",
    }
    with pytest.raises(ValueError, match="cyclic"):
        _topological_order(ddls)


def test_skip_hnsw_drops_live_index(hnsw_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    compact_database(hnsw_db, target, skip_hnsw=True)

    assert _hnsw_index_names(target) == []


def test_skip_hnsw_writes_recipe(hnsw_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    compact_database(hnsw_db, target, skip_hnsw=True)

    conn = duckdb.connect(str(target), read_only=True)
    try:
        rows = conn.execute(
            "SELECT index_name, table_name, column_name, metric FROM _compactor_hnsw_recipe"
        ).fetchall()
    finally:
        conn.close()

    # Metric recovered via pragma_hnsw_index_info(); the catalog DDL strips WITH (...).
    assert rows == [("vec_idx", "vectors", "embedding", "l2sq")]


def test_capture_hnsw_recipes_rejects_unparseable_ddl() -> None:
    # DDL with USING HNSW but no parenthesized column can't occur from real
    # DuckDB; the helper is the only place the parse contract is observable.
    conn = duckdb.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="could not parse HNSW column"):
            _capture_hnsw_recipes(conn, [("idx", "tbl", "CREATE INDEX idx ON tbl USING HNSW")])
    finally:
        conn.close()


def _hnsw_metrics(database: Path) -> dict[str, str]:
    conn = duckdb.connect(str(database), read_only=True)
    try:
        conn.execute(f"LOAD '{_bundled_extension_path('vss')}'")
        return dict(
            conn.execute("SELECT index_name, metric FROM pragma_hnsw_index_info()").fetchall()
        )
    finally:
        conn.close()


@pytest.fixture
def cosine_hnsw_db(tmp_path: Path) -> Path:
    """A DuckDB file whose HNSW index is built WITH (metric = 'cosine')."""
    db_path = tmp_path / "cosine.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(f"LOAD '{_bundled_extension_path('vss')}'")
        conn.execute("SET hnsw_enable_experimental_persistence = true")
        conn.execute("CREATE TABLE vectors (id INTEGER, embedding FLOAT[4])")
        conn.execute(
            "INSERT INTO vectors SELECT range, "
            "[random(), random(), random(), random()]::FLOAT[4] FROM range(50)"
        )
        conn.execute(
            "CREATE INDEX cos_idx ON vectors USING HNSW (embedding) WITH (metric = 'cosine')"
        )
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    return db_path


def test_rebuild_preserves_hnsw_metric(cosine_hnsw_db: Path, tmp_path: Path) -> None:
    # Regression guard: the catalog DDL strips WITH (...), so a verbatim
    # rebuild would silently reset cosine -> l2sq. The metric must round-trip
    # via pragma_hnsw_index_info().
    target = tmp_path / "out.duckdb"
    compact_database(cosine_hnsw_db, target)
    assert _hnsw_metrics(target) == {"cos_idx": "cosine"}


def test_rebuild_preserves_metric_on_real_fixture(shopware_cli_index: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    compact_database(shopware_cli_index, target)
    metrics = _hnsw_metrics(target)
    assert metrics
    assert all(m == "cosine" for m in metrics.values())


def test_rebuild_orders_fk_tables(tmp_path: Path) -> None:
    # Child table sorts alphabetically before its parent; a naive
    # alphabetical/catalog-order rebuild would insert children first and trip
    # the FK. Topological order must place the parent first.
    src = tmp_path / "fk.duckdb"
    conn = duckdb.connect(str(src))
    try:
        conn.execute("CREATE TABLE owners (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE cats (id INTEGER PRIMARY KEY, owner_id INTEGER, "
            "FOREIGN KEY (owner_id) REFERENCES owners(id))"
        )
        conn.execute("INSERT INTO owners SELECT range FROM range(10)")
        conn.execute("INSERT INTO cats SELECT range, range % 10 FROM range(40)")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    target = tmp_path / "fk-out.duckdb"
    compact_database(src, target)

    out = duckdb.connect(str(target), read_only=True)
    try:
        assert out.execute("SELECT count(*) FROM owners").fetchone()[0] == 10
        assert out.execute("SELECT count(*) FROM cats").fetchone()[0] == 40
    finally:
        out.close()


def test_compact_rejects_non_main_schema(tmp_path: Path) -> None:
    src = tmp_path / "schemas.duckdb"
    conn = duckdb.connect(str(src))
    try:
        conn.execute("CREATE SCHEMA extra")
        conn.execute("CREATE TABLE extra.t (id INTEGER)")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    target = tmp_path / "out.duckdb"
    with pytest.raises(ValueError, match="non-main schema"):
        compact_database(src, target)
    assert not target.exists()


def test_compact_rejects_views(tmp_path: Path) -> None:
    src = tmp_path / "views.duckdb"
    conn = duckdb.connect(str(src))
    try:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("CREATE VIEW v AS SELECT * FROM t")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    target = tmp_path / "out.duckdb"
    with pytest.raises(ValueError, match="view"):
        compact_database(src, target)
    assert not target.exists()


def test_compact_rejects_user_defined_types(tmp_path: Path) -> None:
    # vss inlines ENUM in the rebuilt DDL; without refusing here, the user's
    # CREATE TYPE is silently dropped and downstream queries that cast to the
    # type ('x'::color) break.
    src = tmp_path / "types.duckdb"
    conn = duckdb.connect(str(src))
    try:
        conn.execute("CREATE TYPE color AS ENUM ('r', 'g', 'b')")
        conn.execute("CREATE TABLE t (id INTEGER, c color)")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    target = tmp_path / "out.duckdb"
    with pytest.raises(ValueError, match="user-defined type"):
        compact_database(src, target)
    assert not target.exists()


def test_compact_rejects_generated_columns(tmp_path: Path) -> None:
    # Generated columns crash the INSERT step opaquely today (the rebuild emits
    # an explicit column list including the virtual column). Refuse at the gate.
    src = tmp_path / "gen.duckdb"
    conn = duckdb.connect(str(src))
    try:
        conn.execute("CREATE TABLE g (id INTEGER, doubled INTEGER GENERATED ALWAYS AS (id * 2))")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    target = tmp_path / "out.duckdb"
    with pytest.raises(ValueError, match="generated column"):
        compact_database(src, target)
    assert not target.exists()


def test_compact_rejects_self_referential_fk(tmp_path: Path) -> None:
    # duckdb_tables().sql emits invalid DDL for self-ref FK (drops the FK clause
    # and leaves a trailing comma); today this crashes the rebuild at CREATE
    # TABLE with an opaque parser error. Refuse at the gate.
    src = tmp_path / "selfref.duckdb"
    conn = duckdb.connect(str(src))
    try:
        conn.execute(
            "CREATE TABLE node (id INTEGER PRIMARY KEY, parent INTEGER, "
            "FOREIGN KEY (parent) REFERENCES node(id))"
        )
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    target = tmp_path / "out.duckdb"
    with pytest.raises(ValueError, match="self-referential foreign key"):
        compact_database(src, target)
    assert not target.exists()


def test_compact_rejects_expression_hnsw_column(tmp_path: Path) -> None:
    # _HNSW_COLUMN_RE truncates an expression key at the first inner ')'.
    # Without refusing, --skip-hnsw records a malformed column string and
    # `restore` later crashes on the unbalanced DDL.
    src = tmp_path / "expr-hnsw.duckdb"
    vss_path = _bundled_extension_path("vss")
    conn = duckdb.connect(str(src))
    try:
        conn.execute(f"LOAD '{vss_path}'")
        conn.execute("SET hnsw_enable_experimental_persistence = true")
        conn.execute("CREATE TABLE v (id INTEGER, raw FLOAT[4])")
        conn.execute(
            "INSERT INTO v SELECT range, "
            "[random(), random(), random(), random()]::FLOAT[4] FROM range(20)"
        )
        conn.execute("CREATE INDEX vidx ON v USING HNSW (CAST(raw AS FLOAT[4]))")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    target = tmp_path / "out.duckdb"
    with pytest.raises(ValueError, match="non-column expression"):
        compact_database(src, target)
    assert not target.exists()


def test_restore_rebuilds_hnsw_from_recipe(cosine_hnsw_db: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "skipped.duckdb"
    compact_database(cosine_hnsw_db, artifact, skip_hnsw=True)
    assert _hnsw_index_names(artifact) == []

    result = restore_indexes(artifact)

    assert result.restored == ("cos_idx",)
    assert _hnsw_metrics(artifact) == {"cos_idx": "cosine"}


def test_restore_is_idempotent(cosine_hnsw_db: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "skipped.duckdb"
    compact_database(cosine_hnsw_db, artifact, skip_hnsw=True)
    restore_indexes(artifact)

    again = restore_indexes(artifact)

    assert again.restored == ()
    assert _hnsw_metrics(artifact) == {"cos_idx": "cosine"}


def test_restore_without_recipe_fails_hard(populated_db: Path) -> None:
    with pytest.raises(ValueError, match="recipe"):
        restore_indexes(populated_db)


def test_restore_missing_database_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="database not found"):
        restore_indexes(tmp_path / "missing.duckdb")
