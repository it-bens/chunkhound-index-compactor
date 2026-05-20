# Test Independence (reference)

The project uses pytest's `tmp_path` for per-test filesystem state. No `pytest-xdist` today. No module-level mutable state in test files. `conftest.py` carries the shared fixtures; nothing else is global. The committed `shopware-cli-chunks.duckdb` is read-only — every test that opens it does so read-only or copies first into `tmp_path`. `time.time()` / `datetime.now()` appear only inside fixture constructors whose values are never asserted; UUIDs in `tests/fixtures/` are fixed strings.

## Shared mutable state

Tests do not share mutable state across test functions or fixtures. Four real leak vectors:

1. **Module-level `var = [...]`** in a test file, written by one test, read by another
2. **`@pytest.fixture(scope="session")` or `scope="module")` returning a mutable object** subsequently mutated by tests that use it
3. **Closure capture across `@pytest.fixture` and the dependent test** where the fixture mutates a variable later tests read
4. **Global singletons** (`os.environ`, `sys.path`, `logging.getLogger("...").handlers`, monkey-patched module attrs) mutated without restoration

### Do NOT flag

- Read-only values produced once by a session-scoped fixture (a compiled regex, a parsed JSON manifest)
- Values produced fresh by a function-scoped fixture every test (`tmp_path`, the existing `populated_db` / `bloated_db` / `hnsw_db`)
- `pytest.fixture` that opens a DuckDB connection inside `yield` and closes it after — the connection is per-test
- Per-test locals passed explicitly between fixture and dependent test parameter

```python
# WRONG: subsequent test depends on a previous mutation
@pytest.fixture(scope="session")
def shared_conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "shared.duckdb"
    conn = duckdb.connect(str(db))
    yield conn
    conn.close()

def test_first_writes(shared_conn):
    shared_conn.execute("CREATE TABLE t (id INTEGER)")
    shared_conn.execute("INSERT INTO t VALUES (1)")

def test_second_reads(shared_conn):
    row = shared_conn.execute("SELECT count(*) FROM t").fetchone()
    assert row[0] == 1                                # passes only if test_first ran first

# RIGHT: each test owns its DB
def test_writes_to_fresh_db(tmp_path):
    conn = duckdb.connect(str(tmp_path / "src.duckdb"))
    try:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        row = conn.execute("SELECT count(*) FROM t").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 1

# WRONG: mutating sys.path without restoration
def test_imports_local(tmp_path):
    sys.path.insert(0, str(tmp_path))                 # leaks to later tests

# RIGHT: monkeypatch handles restoration
def test_imports_local(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))        # auto-restored on teardown
```

`pytest-xdist` and any future parallel adoption amplify the risk: two parallel tests racing on the same global produce nondeterministic outcomes. Prefer fixture-scoped injection over global mutation.

## `monkeypatch` for any global change

When a test must change a process-wide value (environment variable, module attribute, working directory, `sys.path`), use the `monkeypatch` fixture rather than direct mutation. `monkeypatch` restores every change automatically at fixture teardown.

| Need | `monkeypatch` method |
|---|---|
| Replace a module attribute or class field | `monkeypatch.setattr(target, name, value)` |
| Set an environment variable | `monkeypatch.setenv(name, value)` |
| Remove an environment variable | `monkeypatch.delenv(name)` |
| Inject a path on `sys.path` | `monkeypatch.syspath_prepend(path)` |
| Replace a dict entry | `monkeypatch.setitem(mapping, name, value)` |
| Change the working directory | `monkeypatch.chdir(path)` |

The `with monkeypatch.context() as m:` form scopes the patch to a block within the test rather than the whole test body, useful when an inner section needs the patch but outer assertions don't.

## Non-deterministic inputs

Values that change each run do not feed into assertions.

### Flag

| Call | Context |
|---|---|
| `time.time()`, `datetime.now()`, `time.perf_counter()` | as an asserted value, or encoded into one |
| `random.random()`, `random.randint()` (without seed) | any test use |
| `secrets.token_hex(...)`, `secrets.token_bytes(...)` | asserted |
| `uuid.uuid4()` | asserted |
| `socket.gethostname()` | asserted |
| `os.getpid()` | asserted |

### Skip

| Call | Context |
|---|---|
| `time.time()` | only in fixture constructors whose value is never asserted |
| `datetime(2024, 1, 1, tzinfo=timezone.utc)` | fixed values always OK |
| `random.Random(seed)` | deterministic seed, repeatable |

The current `hnsw_db` fixture uses `random()` inside the DuckDB query to populate vectors. The values are never asserted, only counted; the existing tests assert `count(*)` and the presence of indexes, not vector contents. That is the boundary the rule sits at — non-determinism is fine when it does not feed an assertion.

```python
# WRONG: asserts against a freshly generated UUID
def test_export_produces_id() -> None:
    got = export.new_id()
    assert re.match(r"^[0-9a-f-]{36}$", got)            # tautological; what bug does this catch?

# RIGHT: inject the generator, assert deterministic output
def test_export_uses_provided_id(monkeypatch) -> None:
    monkeypatch.setattr("pkg.export._gen_id", lambda: "fixed-id")
    assert export.new_id() == "fixed-id"

# WRONG: asserts on a wall-clock value
def test_manifest_timestamp() -> None:
    info = manifest.new()
    assert abs(info.created.timestamp() - time.time()) < 5         # flaky

# RIGHT: fix the clock or compare structure
def test_manifest_created_is_populated() -> None:
    info = manifest.new()
    assert info.created is not None
```

## Fixture scope checklist

When defining a `@pytest.fixture`, pick the smallest scope that satisfies the test. Larger scopes amortize setup cost but enable cross-test contamination.

| Scope | When |
|---|---|
| `function` (default) | Anything mutable (DuckDB connections, temp dirs, monkeypatched state). The default for a reason. |
| `class` | Suite of methods on a class sharing read-only setup. The project doesn't use test classes today. |
| `module` | Read-only setup expensive enough to share, with no chance of mutation. Compiled regex, parsed JSON manifest. |
| `session` | Read-only setup shared across the entire run. `shopware_cli_index` would be a candidate if profiling showed function-scope reopening dominated. |

If a fixture sits at scope > function, the fixture's docstring documents the invariant that justifies the scope (read-only, idempotent, etc.) so a later editor doesn't add a mutation.
