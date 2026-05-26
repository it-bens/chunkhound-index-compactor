"""Compact DuckDB databases by streaming them into fresh files.

DuckDB does not reclaim disk space after deletes or drops in its single-file
format, and `COPY FROM DATABASE` races foreign-key enforcement on large
FK-bearing databases (it commits child rows before their parents). This module
rebuilds the database table-by-table in foreign-key-topological order instead:
capture the source schema, recreate sequences/tables/indexes in a fresh file,
and INSERT each table parent-before-child. HNSW vector indexes are recreated
with the metric recovered from `pragma_hnsw_index_info()` (the catalog DDL
strips the `WITH (...)` clause).
"""

from __future__ import annotations

import contextlib
import errno
import shutil
from dataclasses import dataclass
from pathlib import Path

import duckdb
from chunkhound_index_commons.schema import (
    reject_unsupported_objects,
    topological_order,
)
from chunkhound_index_commons.sql import (
    escape_sql_literal,
    is_bare_identifier,
    quote_identifier,
)
from chunkhound_index_commons.vss import (
    capture_hnsw_metrics,
    is_hnsw_index_ddl,
    load_bundled_vss,
    parse_hnsw_column,
    recreate_hnsw_index,
)

# can rebuild the stripped vector indexes. ChunkHound ignores the extra table.
RECIPE_TABLE = "_compactor_hnsw_recipe"


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of a single compaction run."""

    source: Path
    target: Path
    source_size: int
    target_size: int

    @property
    def delta_bytes(self) -> int:
        return self.target_size - self.source_size

    @property
    def delta_pct(self) -> float:
        if self.source_size == 0:
            return 0.0
        return self.delta_bytes / self.source_size * 100


@dataclass(frozen=True)
class RestoreResult:
    """Outcome of a single `restore_indexes` run."""

    database: Path
    restored: tuple[str, ...]


def compact_database(source: Path, target: Path, *, skip_hnsw: bool = False) -> CompactionResult:
    """Rebuild `source` DuckDB database into a fresh file at `target`.

    Streams the source into a freshly-allocated file table-by-table in
    foreign-key-topological order (parent before child), which avoids the FK
    race that `COPY FROM DATABASE` hits on large FK-bearing databases. HNSW
    vector indexes are recreated with the metric recovered from
    `pragma_hnsw_index_info()`.

    With ``skip_hnsw=True`` the vector indexes are not rebuilt; instead a
    `_compactor_hnsw_recipe` table is written so `restore_indexes` can rebuild
    them later on a RAM-capable machine. The output then has no vector index
    and falls back to brute-force search until restored.

    Raises:
        ValueError: `target` resolves to the same path as `source`; the source
            contains a non-`main` schema, a view, a user-defined type, a
            generated column, a self-referential FK, or an HNSW index on a
            non-bare-column expression; or the FK graph contains a cycle.
            Every refusal fires before the target file is created.
        FileNotFoundError: `source` does not exist.
        FileExistsError: `target` already exists.
        RuntimeError: the bundled `vss` extension binary cannot be located.
            Only reachable when the source contains at least one HNSW index.
    """
    if target.resolve() == source.resolve():
        raise ValueError("target path must differ from source")
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")

    src_size = source.stat().st_size
    src_literal = escape_sql_literal(str(source))
    dst_literal = escape_sql_literal(str(target))

    spill_dir = target.parent / ".chunkhound-compactor.tmp"
    try:
        spill_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OSError(f"cannot create spill directory beside target: {e}") from e

    conn = duckdb.connect(":memory:")
    dst_attached = False
    try:
        # Co-locate DuckDB spill with the target's filesystem. DuckDB's default
        # `temp_directory` is the CWD-relative `.tmp`, which can fill a small
        # working filesystem while the destination disk has space.
        conn.execute(f"SET temp_directory = '{escape_sql_literal(str(spill_dir))}'")
        conn.execute(f"ATTACH '{src_literal}' AS src (READ_ONLY)")
        reject_unsupported_objects(conn, database_name="src")

        sequences = [
            sql
            for (sql,) in conn.execute(
                "SELECT sql FROM duckdb_sequences() WHERE database_name = 'src'"
            ).fetchall()
        ]
        table_ddls = dict(
            conn.execute(
                "SELECT table_name, sql FROM duckdb_tables() WHERE database_name = 'src'"
            ).fetchall()
        )
        index_rows = conn.execute(
            "SELECT index_name, table_name, sql FROM duckdb_indexes() "
            "WHERE database_name = 'src' AND sql IS NOT NULL"
        ).fetchall()
        hnsw_indexes = [(n, t, s) for n, t, s in index_rows if is_hnsw_index_ddl(s)]
        plain_index_ddls = [s for _, _, s in index_rows if not is_hnsw_index_ddl(s)]

        recipes = _capture_hnsw_recipes(conn, hnsw_indexes)
        order = topological_order(table_ddls)

        conn.execute(f"ATTACH '{dst_literal}' AS dst")
        dst_attached = True
        conn.execute("USE dst")

        for sql in sequences:
            conn.execute(sql)
        for name in order:
            conn.execute(table_ddls[name])

        conn.execute("SET preserve_insertion_order = false")
        for name in order:
            quoted = quote_identifier(name)
            conn.execute(f"INSERT INTO {quoted} SELECT * FROM src.{quoted}")

        for sql in plain_index_ddls:
            conn.execute(sql)

        if recipes:
            if skip_hnsw:
                _write_recipe_table(conn, recipes)
            else:
                conn.execute("SET hnsw_enable_experimental_persistence = true")
                for name, table, column, metric in recipes:
                    recreate_hnsw_index(conn, name=name, table=table, column=column, metric=metric)

        conn.execute("CHECKPOINT dst")
        conn.execute("USE memory")
        conn.execute("DETACH src")
        conn.execute("DETACH dst")
    except BaseException:
        conn.close()
        if dst_attached:
            if target.exists():
                target.unlink()
            wal = target.with_suffix(target.suffix + ".wal")
            if wal.exists():
                wal.unlink()
        raise
    else:
        conn.close()
    finally:
        # Remove the spill dir when DuckDB never spilled into it. rmdir fails on
        # a non-empty dir, so an in-progress spill is never deleted.
        with contextlib.suppress(OSError):
            spill_dir.rmdir()

    return CompactionResult(
        source=source,
        target=target,
        source_size=src_size,
        target_size=target.stat().st_size,
    )


def _capture_hnsw_recipes(
    conn: duckdb.DuckDBPyConnection, hnsw_indexes: list[tuple[str, str, str]]
) -> list[tuple[str, str, str, str]]:
    """Return (index_name, table_name, column, metric) for each HNSW index.

    Refuses non-bare-column expression keys: the recipe table stores the column
    as a single VARCHAR, with no schema for an arbitrary expression.
    """
    if not hnsw_indexes:
        return []
    load_bundled_vss(conn)
    metrics = capture_hnsw_metrics(conn)
    recipes: list[tuple[str, str, str, str]] = []
    for name, table, sql in hnsw_indexes:
        column = parse_hnsw_column(sql)
        if not is_bare_identifier(column):
            raise ValueError(
                f"HNSW index '{name}' is on a non-column expression (out of scope): {column!r}"
            )
        recipes.append((name, table, column, metrics[name]))
    return recipes


def _write_recipe_table(
    conn: duckdb.DuckDBPyConnection, recipes: list[tuple[str, str, str, str]]
) -> None:
    """Write the `_compactor_hnsw_recipe` table so `restore_indexes` can rebuild."""
    quoted = quote_identifier(RECIPE_TABLE)
    conn.execute(
        f"CREATE TABLE {quoted} "
        "(index_name VARCHAR, table_name VARCHAR, column_name VARCHAR, metric VARCHAR)"
    )
    conn.executemany(f"INSERT INTO {quoted} VALUES (?, ?, ?, ?)", recipes)


def restore_indexes(database: Path) -> RestoreResult:
    """Rebuild HNSW indexes recorded in a `--skip-hnsw` artifact's recipe table.

    Operates in place on `database`. Recreates each recipe index that does not
    already exist (idempotent), using the metric recorded at compaction time.

    Rebuilding an HNSW index loads it fully into RAM, so this needs the memory
    that `--skip-hnsw` avoided. Run it on a RAM-capable machine.

    Raises:
        FileNotFoundError: `database` does not exist.
        ValueError: `database` has no `_compactor_hnsw_recipe` table (it is not
            a `--skip-hnsw` artifact, so there is nothing to restore).
        RuntimeError: the bundled `vss` extension binary cannot be located.
    """
    if not database.is_file():
        raise FileNotFoundError(f"database not found: {database}")

    conn = duckdb.connect(str(database))
    try:
        has_recipe = conn.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [RECIPE_TABLE]
        ).fetchone()
        if not has_recipe or not has_recipe[0]:
            raise ValueError(f"no {RECIPE_TABLE} table found: not a --skip-hnsw artifact")

        recipes = conn.execute(
            f"SELECT index_name, table_name, column_name, metric "
            f"FROM {quote_identifier(RECIPE_TABLE)}"
        ).fetchall()
        existing = {
            name for (name,) in conn.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
        }

        load_bundled_vss(conn)
        conn.execute("SET hnsw_enable_experimental_persistence = true")

        restored: list[str] = []
        for name, table, column, metric in recipes:
            if name in existing:
                continue
            recreate_hnsw_index(conn, name=name, table=table, column=column, metric=metric)
            restored.append(name)

        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    return RestoreResult(database=database, restored=tuple(restored))


def replace_with_compacted(source: Path, compacted: Path) -> Path:
    """Atomically replace `source` with `compacted`; move the original to `<source>.bak`.

    Returns the backup path. Refuses to overwrite an existing backup.

    Raises:
        FileNotFoundError: `source` or `compacted` is missing.
        FileExistsError: backup path already exists.
        OSError: the move from `compacted` to `source` fails even via the
            cross-filesystem fallback (`shutil.move`).
    """
    if not source.is_file():
        raise FileNotFoundError(f"source not found: {source}")
    if not compacted.is_file():
        raise FileNotFoundError(f"compacted file not found: {compacted}")

    backup = source.with_suffix(source.suffix + ".bak")
    if backup.exists():
        raise FileExistsError(f"backup path already exists: {backup}")

    source.rename(backup)
    try:
        compacted.rename(source)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        # `compacted` lives on a different filesystem from `source` (the user
        # passed an explicit target on another mount). `Path.rename` raises
        # EXDEV for cross-device moves; `shutil.move` copies + deletes across
        # filesystems.
        shutil.move(str(compacted), str(source))
    return backup


def human_size(num_bytes: float) -> str:
    """Format a byte count as a 1-decimal binary-prefix string (KiB, MiB, GiB...)."""
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"
