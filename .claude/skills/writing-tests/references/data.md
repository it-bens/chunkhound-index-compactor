# Test Data and Fixtures (reference)

## File dependency must be locatable from the test

External file dependencies (`open()`, `Path.read_bytes()`, fixture loaders) point to a file the reader can locate from the test file.

### Acceptable

- `tests/fixtures/` referenced via a fixture defined in `tests/conftest.py` (see `shopware_cli_index`)
- `tmp_path` for files the test itself creates
- A `tests/<surface>_fixtures/` subdirectory when one fixture set is local to one test module (rare)
- `importlib.resources` for fixtures shipped inside the package (none today; the current fixtures are test-only)

### Flag

- Absolute paths (`/home/user/data.duckdb`, `/tmp/chunkhound-fixture`)
- Source-tree access (`../../src/chunkhound_index_compactor/core.py` opened at test time)
- Cross-package fixture borrow (`../../other-tool/tests/fixtures/...`)
- Dynamic globs over an unbounded directory
- Reading from `~/...` (the test depends on the developer's home dir)

```python
# WRONG: absolute path, flaky across machines
data = Path("/home/me/samples/index.duckdb").read_bytes()

# WRONG: reaching across into another tool's fixtures
data = Path("../../chunkhound/tests/fixtures/index.duckdb").read_bytes()

# RIGHT: package-local fixture under tests/fixtures/, surfaced through conftest
def test_thing(shopware_cli_index: Path, tmp_path: Path) -> None:
    compact_database(shopware_cli_index, tmp_path / "out.duckdb")

# RIGHT: tmp_path for state the test creates
def test_creates_recipe(tmp_path: Path) -> None:
    db = tmp_path / "src.duckdb"
    # ... build the DB inline using duckdb.connect
```

## Descriptive identifiers in code

String literals used as identifiers in assertions are descriptive. Opaque hex blobs make failure messages unreadable.

### Flag

- 32 consecutive hex characters used as a test-constructed identifier: `"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"`
- Repeated-character placeholders: `"0000000000000001"`, `"ffffffffffffffff"`
- Placeholder UUIDs invented for the test body

### Do NOT flag

- UUIDs read from or written to `tests/fixtures/` (they mirror real on-disk shapes)
- Identifiers produced by the code under test (captured in a var named `generated`)
- Tests that specifically exercise UUID-format validation

```python
# WRONG: unreadable in a failure message
conn.execute("INSERT INTO sessions VALUES (?)", ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"])
got = load_session(db, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
assert got.id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

# RIGHT: descriptive
conn.execute("INSERT INTO sessions VALUES (?)", ["primary-session"])
got = load_session(db, "primary-session")
assert got.id == "primary-session"
```

### Descriptive-name conventions

| Context | Good |
|---|---|
| Table names in inline DDL | `items`, `owners`, `cats`, `vectors` |
| Source / target paths | `tmp_path / "src.duckdb"`, `tmp_path / "out.duckdb"` |
| HNSW index names | `vec_idx`, `cos_idx` |
| FK example tables | `owners`, `cats` (the existing `test_rebuild_orders_fk_tables` shape) |

## Real fixture files for parsers and complex I/O

Tests exercising parsing or complex I/O read real fixture files from `tests/fixtures/` rather than build content inline. The current canonical example is `shopware_cli_index` — a committed 204 MiB ChunkHound index used by `test_rebuild_preserves_metric_on_real_fixture` and `test_compact_shopware_cli_index_shrinks_substantially`.

### Applies when

- Test writes a multi-line DuckDB DDL blob, then reads it back, and the blob is longer than ~10 lines
- Test exercises a parser, importer, or scanner against representative input
- Test needs a DuckDB file with a specific real-world shape (HNSW metric, FK chain depth) that's tedious to reconstruct

### Does not apply when

- Blob is a single statement (`CREATE TABLE items AS SELECT range AS id FROM range(100)`)
- Test specifically exercises a malformed input shape (inline is clearer than a dedicated fixture per malformation)
- Content is not written to any file or stream

```python
# WRONG: 40-line inline DDL builds a synthetic ChunkHound shape
def test_compact_chunkhound_shape(tmp_path: Path) -> None:
    src = tmp_path / "src.duckdb"
    conn = duckdb.connect(str(src))
    conn.execute("CREATE TABLE chunks (...) ...")
    # ... 38 more statements building the schema
    conn.close()
    compact_database(src, tmp_path / "out.duckdb")

# RIGHT: the fixture lives in tests/fixtures/ where it can be inspected and reused
def test_compact_chunkhound_shape(shopware_cli_index: Path, tmp_path: Path) -> None:
    compact_database(shopware_cli_index, tmp_path / "out.duckdb")
```

### Adding a new committed fixture

When a new fixture lands under `tests/fixtures/`:

1. Add a `@pytest.fixture` in `tests/conftest.py` that returns the file path. Document the provenance in the docstring (how the file was generated, what it represents).
2. The `tests/fixtures/.gitignore` already ignores `*.compacted.duckdb` and `*.bak` so manual runs don't pollute the tree. Extend it if a new generated artifact needs ignoring.
3. Reference the fixture by name in the tests that need it. Don't `Path(__file__).parent / "fixtures" / "..."` directly — go through the conftest fixture so the path lives in one place.

## Helper extraction for repeated arrange code

If two or more tests repeat 5+ consecutive lines of construction with identical types and arguments, extract a fixture.

### Extraction patterns (pytest)

| Pattern | Use when |
|---|---|
| Module-local `@pytest.fixture` (defined inside the test file) | Most common. Test-module-local, single-use across that module's tests. |
| Fixture in `tests/conftest.py` | Three or more test files need the fixture. Promote the module-local fixture to conftest when the third user appears. |
| Fixture with `yield` and post-yield cleanup | Resources that need teardown (open DuckDB connections, spawned subprocesses, monkeypatch under `with`). |
| Parametrized fixture (`@pytest.fixture(params=[...])`) | When the same setup needs to run against multiple variants and every test using it wants all variants. |
| `tmp_path_factory` for session-shared expensive setup | Large committed fixtures the test can read-only (the current `shopware_cli_index` is shared via function-scoped fixture; switch to session if profiling shows it dominates). |

### Do NOT extract

- Helper would hide the single input that varies per test (the variation is the test)
- Fewer than 5 repeated lines
- Only two current occurrences; wait for the third before extracting

```python
# WRONG: same 7-line setup repeated across three tests
def test_a(tmp_path: Path) -> None:
    db = tmp_path / "src.duckdb"
    conn = duckdb.connect(str(db))
    try:
        conn.execute(f"LOAD '{_bundled_extension_path('vss')}'")
        conn.execute("SET hnsw_enable_experimental_persistence = true")
        conn.execute("CREATE TABLE v (id INTEGER, e FLOAT[4])")
        conn.execute("INSERT INTO v SELECT range, [random(),random(),random(),random()]::FLOAT[4] FROM range(50)")
        conn.execute("CREATE INDEX i ON v USING HNSW (e) WITH (metric = 'cosine')")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    # ... act + assert

# RIGHT: shared fixture; the current cosine_hnsw_db fixture in test_rebuild.py follows this shape
@pytest.fixture
def cosine_hnsw_db(tmp_path: Path) -> Path:
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
        conn.execute("CREATE INDEX cos_idx ON vectors USING HNSW (embedding) WITH (metric = 'cosine')")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    return db_path
```

### Placement

- Module-local fixtures go **above** the tests that use them (Python convention: definitions before uses).
- Conftest fixtures live in `tests/conftest.py`; pytest discovers them across the suite without explicit imports.
- Fixture names start lowercase (snake_case). The name is the parameter name in dependent tests.
- Cleanup uses `yield` + post-yield statements, or `request.addfinalizer(...)`. Avoid `finally:` outside the fixture body for teardown; the fixture lifecycle owns it.

## Production-scale large fixtures

A committed fixture exceeding a few MiB is in scope when it exercises behavior small synthetic data cannot. The current `shopware_cli_index` (~204 MiB) is the only such fixture today; it sits in `tests/fixtures/`, has its provenance in the conftest docstring, and is referenced by tests that need a real ChunkHound-shaped index.

### When to add another

Only when:

1. The smaller synthetic fixtures (`populated_db`, `bloated_db`, `hnsw_db`) cannot exercise the path the test targets.
2. A real-world artifact is the cheapest path to that path's coverage (extension version churn, accumulated bloat patterns, real FK depth).
3. The size is bounded (single-digit hundreds of MiB at most; the repo is not a fixture vault).

When committing a new large fixture, add `*.compacted.duckdb` or similar derived patterns to `tests/fixtures/.gitignore` so manual runs don't pollute the repo with regenerated outputs.
