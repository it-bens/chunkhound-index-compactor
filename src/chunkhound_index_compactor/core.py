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
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import duckdb

_HNSW_RE = re.compile(r"USING\s+HNSW", re.IGNORECASE)
_HNSW_COLUMN_RE = re.compile(r"USING\s+HNSW\s*\(\s*(.+?)\s*\)", re.IGNORECASE)
_FK_REFERENCES_RE = re.compile(r"FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+(\w+)", re.IGNORECASE)
_GENERATED_COLUMN_RE = re.compile(r"\bGENERATED\s+ALWAYS\b", re.IGNORECASE)
_BARE_IDENTIFIER_RE = re.compile(r'^(?:[A-Za-z_]\w*|"(?:[^"]|"")+")$')

# Name of the table `--skip-hnsw` writes into the output so `restore_indexes`
# can rebuild the stripped vector indexes. ChunkHound ignores the extra table.
RECIPE_TABLE = "_compactor_hnsw_recipe"

# DuckDB writes "DUCK" at byte offset 8 of a database's main header (an 8-byte
# checksum precedes it). Identifies the real database among a ChunkHound index
# directory's sidecars (`*.root.json`, `*.wal`) regardless of filename.
_DUCKDB_MAGIC = b"DUCK"
_ROOT_JSON_SUFFIX = ".root.json"


def _is_duckdb_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as fh:
            header = fh.read(12)
    except OSError:
        # An unreadable sibling is simply not a candidate; surfacing it would
        # abort the directory scan that builds the "did you mean" suggestion.
        return False
    return header[8:12] == _DUCKDB_MAGIC


def _resolve_source(source: Path) -> Path:
    """Resolve a directory `source` to the single DuckDB file it unambiguously holds.

    A directory carrying exactly one ChunkHound index — a `<name>.root.json`
    sidecar whose sibling `<name>` is a valid DuckDB file — resolves to that
    file. Any other directory shape is refused, listing the DuckDB files found
    (by magic bytes) as suggestions. Non-directory paths pass through unchanged.

    Raises:
        FileNotFoundError: `source` is a directory that does not resolve to a
            single ChunkHound index.
    """
    if not source.is_dir():
        return source

    sidecar_dbs: list[Path] = []
    for sidecar in source.glob(f"*{_ROOT_JSON_SUFFIX}"):
        db = sidecar.with_name(sidecar.name[: -len(_ROOT_JSON_SUFFIX)])
        if _is_duckdb_file(db):
            sidecar_dbs.append(db)
    if len(sidecar_dbs) == 1:
        return sidecar_dbs[0]

    candidates = sorted(p for p in source.iterdir() if _is_duckdb_file(p))
    if candidates:
        listing = "\n".join(f"  {p}" for p in candidates)
        raise FileNotFoundError(
            f"{source} is a directory; did you mean one of these DuckDB files:\n{listing}"
        )
    raise FileNotFoundError(f"no DuckDB database found in directory: {source}")


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
    """Fail hard at the front gate for source shapes we cannot faithfully rebuild.

    `duckdb_schemas()` marks the source's own `main` as `internal = true`, so
    non-main objects are detected via the tables/views catalogs filtered on
    `schema_name`, not on the `internal` flag.

    Each refusal converts a downstream silent-loss or opaque-crash path
    (verified on DuckDB 1.5.2) into a clear pre-target error.
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

    # User-defined types: `duckdb_tables().sql` inlines ENUM/STRUCT/alias
    # definitions, so without refusing here the user's `CREATE TYPE` is dropped
    # silently and any downstream `value::<type>` cast against the rebuilt DB
    # fails with "type does not exist".
    user_types = conn.execute(
        "SELECT type_name FROM duckdb_types() WHERE database_name = 'src' AND NOT internal"
    ).fetchall()
    if user_types:
        names = ", ".join(t for (t,) in user_types)
        raise ValueError(f"source contains user-defined types (out of scope): {names}")

    # Generated columns: `duckdb_tables().sql` emits the virtual column in the
    # column list, but DuckDB rejects it as a target for `INSERT ... SELECT *`,
    # so the rebuild crashes opaquely at the per-table insert.
    table_rows: list[tuple[str, str]] = conn.execute(
        "SELECT table_name, sql FROM duckdb_tables() WHERE database_name = 'src'"
    ).fetchall()
    generated = [name for name, sql in table_rows if _GENERATED_COLUMN_RE.search(sql)]
    if generated:
        raise ValueError(
            f"source contains tables with generated columns (out of scope): {', '.join(generated)}"
        )

    # Self-referential FKs: `duckdb_tables().sql` drops the FK clause and leaves
    # a trailing comma in the column list, so on older DuckDB versions the
    # rebuild crashes at `CREATE TABLE`, and on lenient versions the FK is
    # silently lost. Either way it's an unsupported shape.
    self_ref = conn.execute(
        "SELECT DISTINCT table_name FROM duckdb_constraints() "
        "WHERE database_name = 'src' "
        "AND constraint_type = 'FOREIGN KEY' "
        "AND table_name = referenced_table"
    ).fetchall()
    if self_ref:
        names = ", ".join(t for (t,) in self_ref)
        raise ValueError(
            f"source contains tables with self-referential foreign keys (out of scope): {names}"
        )


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
    src_literal = _escape_sql_literal(str(source))
    dst_literal = _escape_sql_literal(str(target))

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
        conn.execute(f"SET temp_directory = '{_escape_sql_literal(str(spill_dir))}'")
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
            quoted = _quote_identifier(name)
            conn.execute(f"INSERT INTO {quoted} SELECT * FROM src.{quoted}")

        for sql in plain_index_ddls:
            conn.execute(sql)

        if recipes:
            if skip_hnsw:
                _write_recipe_table(conn, recipes)
            else:
                conn.execute("SET hnsw_enable_experimental_persistence = true")
                for name, table, column, metric in recipes:
                    conn.execute(
                        f"CREATE INDEX {_quote_identifier(name)} ON {_quote_identifier(table)} "
                        f"USING HNSW ({column}) WITH (metric = '{_escape_sql_literal(metric)}')"
                    )

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
        column = match.group(1)
        # vss accepts expression keys (e.g. `CAST(col AS FLOAT[N])`), but
        # `_HNSW_COLUMN_RE` is non-greedy and truncates the captured group at
        # the first inner `)`. Refusing non-bare columns here keeps the recipe
        # round-trip honest and turns the deferred restore-time DDL crash into
        # a clear pre-target error.
        if not _BARE_IDENTIFIER_RE.match(column):
            raise ValueError(
                f"HNSW index '{name}' is on a non-column expression (out of scope): {column!r}"
            )
        recipes.append((name, table, column, metrics[name]))
    return recipes


def _write_recipe_table(
    conn: duckdb.DuckDBPyConnection, recipes: list[tuple[str, str, str, str]]
) -> None:
    """Write the `_compactor_hnsw_recipe` table so `restore_indexes` can rebuild."""
    quoted = _quote_identifier(RECIPE_TABLE)
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
            f"FROM {_quote_identifier(RECIPE_TABLE)}"
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
                f"CREATE INDEX {_quote_identifier(name)} ON {_quote_identifier(table)} "
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


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _quote_identifier(name: str) -> str:
    """Wrap `name` in double quotes, doubling any embedded `"`."""
    return '"' + name.replace('"', '""') + '"'
