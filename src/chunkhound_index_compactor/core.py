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

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

_HNSW_RE = re.compile(r"USING\s+HNSW", re.IGNORECASE)
_HNSW_COLUMN_RE = re.compile(r"USING\s+HNSW\s*\(\s*(.+?)\s*\)", re.IGNORECASE)
_FK_REFERENCES_RE = re.compile(r"FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+(\w+)", re.IGNORECASE)

# Name of the table `--skip-hnsw` writes into the output so `restore_indexes`
# can rebuild the stripped vector indexes. ChunkHound ignores the extra table.
RECIPE_TABLE = "_compactor_hnsw_recipe"


def _referenced_tables(ddl: str) -> set[str]:
    """Return the set of table names referenced by FOREIGN KEY clauses in `ddl`."""
    return {m.group(1) for m in _FK_REFERENCES_RE.finditer(ddl)}


def _topological_order(table_ddls: dict[str, str]) -> list[str]:
    """Order table names so every FK parent precedes its children.

    Parent-before-child INSERT is what avoids the FK race that breaks
    `COPY FROM DATABASE` on FK-bearing sources at scale: COPY commits child
    rows in parallel before their parents and aborts with a non-deterministic
    FK violation. Streaming one table at a time in this order sidesteps it.

    `table_ddls` maps table name to its CREATE TABLE DDL. FK targets outside the
    map (none, in practice) are ignored. Independent tables keep input order.

    Raises:
        ValueError: the FK graph contains a cycle.
    """
    deps = {
        name: _referenced_tables(ddl) & (table_ddls.keys() - {name})
        for name, ddl in table_ddls.items()
    }
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(table_ddls)
    while remaining:
        progressed = False
        for name in list(remaining):
            if deps[name] <= placed:
                ordered.append(name)
                placed.add(name)
                remaining.remove(name)
                progressed = True
        if not progressed:
            raise ValueError(f"cyclic foreign-key dependency among tables: {remaining}")
    return ordered


def _reject_unsupported_objects(conn: duckdb.DuckDBPyConnection) -> None:
    """Fail hard if the attached `src` has non-main schemas or any view.

    `duckdb_schemas()` marks the source's own `main` as `internal = true`, so
    detect non-main objects via the tables/views catalogs filtered on
    `schema_name`, not on the `internal` flag.
    """
    non_main = conn.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() "
        "WHERE database_name = 'src' AND schema_name != 'main'"
    ).fetchall()
    if non_main:
        names = ", ".join(f"{s}.{t}" for s, t in non_main)
        raise ValueError(f"source contains non-main schema objects (out of scope): {names}")

    views = conn.execute(
        "SELECT view_name FROM duckdb_views() WHERE database_name = 'src'"
    ).fetchall()
    if views:
        names = ", ".join(v for (v,) in views)
        raise ValueError(f"source contains views (out of scope): {names}")


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
        ValueError: `target` resolves to the same path as `source`, or the
            source schema contains a foreign-key cycle.
        FileNotFoundError: `source` does not exist.
        FileExistsError: `target` already exists.
    """
    if target.resolve() == source.resolve():
        raise ValueError("target path must differ from source")
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")

    src_size = source.stat().st_size
    src_literal = _escape_sql_literal(str(source))
    dst_literal = _escape_sql_literal(str(target))

    conn = duckdb.connect(":memory:")
    dst_attached = False
    try:
        conn.execute(f"ATTACH '{src_literal}' AS src (READ_ONLY)")
        _reject_unsupported_objects(conn)

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
        hnsw_indexes = [(n, t, s) for n, t, s in index_rows if _HNSW_RE.search(s)]
        plain_index_ddls = [s for _, _, s in index_rows if not _HNSW_RE.search(s)]

        recipes = _capture_hnsw_recipes(conn, hnsw_indexes)
        order = _topological_order(table_ddls)

        conn.execute(f"ATTACH '{dst_literal}' AS dst")
        dst_attached = True
        conn.execute("USE dst")

        for sql in sequences:
            conn.execute(sql)
        for name in order:
            conn.execute(table_ddls[name])

        conn.execute("SET preserve_insertion_order = false")
        for name in order:
            conn.execute(f'INSERT INTO "{name}" SELECT * FROM src."{name}"')

        for sql in plain_index_ddls:
            conn.execute(sql)

        if recipes:
            if skip_hnsw:
                _write_recipe_table(conn, recipes)
            else:
                conn.execute("SET hnsw_enable_experimental_persistence = true")
                for name, table, column, metric in recipes:
                    conn.execute(
                        f'CREATE INDEX "{name}" ON "{table}" '
                        f"USING HNSW ({column}) WITH (metric = '{_escape_sql_literal(metric)}')"
                    )

        conn.execute("CHECKPOINT dst")
        conn.execute("USE memory")
        conn.execute("DETACH src")
        conn.execute("DETACH dst")
    except BaseException:
        conn.close()
        if dst_attached and target.exists():
            target.unlink()
        raise
    conn.close()

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

    The metric is recovered from `pragma_hnsw_index_info()` because the catalog
    DDL strips the `WITH (...)` clause; the column is parsed from the DDL.
    """
    if not hnsw_indexes:
        return []
    _load_bundled_extension(conn, "vss")
    metrics = dict(
        conn.execute("SELECT index_name, metric FROM pragma_hnsw_index_info()").fetchall()
    )
    recipes: list[tuple[str, str, str, str]] = []
    for name, table, sql in hnsw_indexes:
        match = _HNSW_COLUMN_RE.search(sql)
        if not match:
            raise ValueError(f"could not parse HNSW column from index DDL: {sql!r}")
        recipes.append((name, table, match.group(1), metrics[name]))
    return recipes


def _write_recipe_table(
    conn: duckdb.DuckDBPyConnection, recipes: list[tuple[str, str, str, str]]
) -> None:
    """Write the `_compactor_hnsw_recipe` table so `restore_indexes` can rebuild."""
    conn.execute(
        f'CREATE TABLE "{RECIPE_TABLE}" '
        "(index_name VARCHAR, table_name VARCHAR, column_name VARCHAR, metric VARCHAR)"
    )
    conn.executemany(f'INSERT INTO "{RECIPE_TABLE}" VALUES (?, ?, ?, ?)', recipes)


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
            f'SELECT index_name, table_name, column_name, metric FROM "{RECIPE_TABLE}"'
        ).fetchall()
        existing = {
            name for (name,) in conn.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
        }

        _load_bundled_extension(conn, "vss")
        conn.execute("SET hnsw_enable_experimental_persistence = true")

        restored: list[str] = []
        for name, table, column, metric in recipes:
            if name in existing:
                continue
            conn.execute(
                f'CREATE INDEX "{name}" ON "{table}" '
                f"USING HNSW ({column}) WITH (metric = '{_escape_sql_literal(metric)}')"
            )
            restored.append(name)

        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    return RestoreResult(database=database, restored=tuple(restored))


def _load_bundled_extension(conn: duckdb.DuckDBPyConnection, ext: str) -> None:
    """LOAD `ext` into `conn` from its bundled `.duckdb_extension` binary."""
    path = _bundled_extension_path(ext)
    conn.execute(f"LOAD '{_escape_sql_literal(str(path))}'")


def _bundled_extension_path(ext: str) -> Path:
    """Locate the bundled `.duckdb_extension` binary for `ext`.

    Raises:
        RuntimeError: the bundled package for `ext` is not importable, or it
            does not contain a matching `.duckdb_extension` binary.
    """
    if ext == "vss":
        import duckdb_extension_vss

        pkg_root = Path(duckdb_extension_vss.__file__).parent / "extensions"
        candidates = sorted(pkg_root.glob(f"v*/{ext}.duckdb_extension"))
        if not candidates:
            raise RuntimeError(f"no bundled {ext}.duckdb_extension found under {pkg_root}")
        return candidates[-1]
    raise RuntimeError(f"no bundled extension known for {ext!r}")


def replace_with_compacted(source: Path, compacted: Path) -> Path:
    """Atomically replace `source` with `compacted`; move the original to `<source>.bak`.

    Returns the backup path. Refuses to overwrite an existing backup.

    Raises:
        FileNotFoundError: `source` or `compacted` is missing.
        FileExistsError: backup path already exists.
    """
    if not source.is_file():
        raise FileNotFoundError(f"source not found: {source}")
    if not compacted.is_file():
        raise FileNotFoundError(f"compacted file not found: {compacted}")

    backup = source.with_suffix(source.suffix + ".bak")
    if backup.exists():
        raise FileExistsError(f"backup path already exists: {backup}")

    source.rename(backup)
    compacted.rename(source)
    return backup


def human_size(num_bytes: float) -> str:
    """Format a byte count as a 1-decimal binary-prefix string (KiB, MiB, GiB...)."""
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")
