# Test Shape (reference)

## AAA structure

Tests with 5+ statements separate arrange, act, and assert phases. Assertions live after the final act, not interspersed.

### Skip

- Tests under 5 statements
- Parametrized cases with 2-3 statements in the body
- Exception-path tests (`with pytest.raises(...)` is a two-phase shape: arrange → expect+act-inside-block, which is fine)

```python
# WRONG: assertions scattered through the body
def test_compact_roundtrip(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    assert not target.exists()                       # assertion in arrange
    result = compact_database(populated_db, target)
    assert isinstance(result, CompactionResult)      # trivial mid-act assertion
    conn = duckdb.connect(str(target), read_only=True)
    row = conn.execute("SELECT count(*) FROM items").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 100

# RIGHT: arrange, act, assert
def test_compact_roundtrips_data(populated_db: Path, tmp_path: Path) -> None:
    # Arrange
    target = tmp_path / "out.duckdb"

    # Act
    compact_database(populated_db, target)

    # Assert
    conn = duckdb.connect(str(target), read_only=True)
    try:
        row = conn.execute("SELECT count(*) FROM items").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 100
```

Comment banners are optional; blank lines between phases are enough when sections are short.

## No conditional logic in tests

Test bodies do not contain conditional logic that picks between assertions.

### Prohibited

- `if`/`else` selecting which assertion runs
- `match` dispatching on test expectations
- Loops with per-iteration branching on expectations
- Ternary-style `(a if cond else b)` for assertion control flow

### Carve-outs

Not violations:

| Pattern | Why |
|---|---|
| `with pytest.raises(...)` for error-path tests | Idiomatic pytest dispatch for error-vs-success. Bounded, single shape. |
| `for tc in cases: ...` (manual loop) | Replace with `@pytest.mark.parametrize`. Manual loops in tests are a smell. |
| `pytest.skip(...)` based on `sys.platform` / `runtime` checks | Platform gate, not assertion logic. |
| `@pytest.mark.slow` deselected by default | Standard slow-test gate. |
| `pytest.raises(...)` short-circuiting on precondition failure | Not an author-written conditional. |

The `expect_error`-vs-success carve-out applies **only** when the success path's assertions are identical across cases. If cases need different positive assertions, split into two parametrize tables or two test functions.

```python
# WRONG: branch picks between two different positive assertions
@pytest.mark.parametrize(("payload", "want_valid", "want_len"), [
    ("john", True, 4),
    ("jöhn", True, 4),
    ("", False, 0),
    ("   ", False, 0),
])
def test_validate(payload, want_valid, want_len) -> None:
    got = validate(payload)
    if want_valid:
        assert got is not None
        assert got.length == want_len
    else:
        assert got is None

# RIGHT: two tables, two test functions
@pytest.mark.parametrize(("payload", "want_len"), [
    ("john", 4),
    ("jöhn", 4),
])
def test_validate_accepts(payload, want_len) -> None:
    got = validate(payload)
    assert got is not None
    assert got.length == want_len

@pytest.mark.parametrize("payload", ["", "   "])
def test_validate_rejects(payload) -> None:
    assert validate(payload) is None
```

### Acceptable expect_error shape

When error vs success is the only branch and the success path has no positive assertions, `pytest.raises` inside a parametrize case works:

```python
@pytest.mark.parametrize(("payload", "expect_error"), [
    pytest.param('{"ok":1}', None, id="well-formed"),
    pytest.param("{ bad", ValueError, id="malformed"),
])
def test_parse(payload, expect_error):
    if expect_error is None:
        parse(payload.encode())   # must not raise
        return
    with pytest.raises(expect_error):
        parse(payload.encode())
```

Preferred when the success path has positive assertions: split into two tests (one for success, one for failure) and skip the branching entirely.

## Assertion scope

Multiple `assert` statements in a test body are acceptable only when they verify a single logical behavior. Unrelated claims belong in separate tests.

Acceptable clusters:

- Multiple properties of one returned object (`result.source_size`, `result.target_size`, `result.delta_pct` after one `compact_database` call)
- Before/after state of one operation
- Related aspects of one behavior

Not acceptable:

- Create + persistence + log line + metric in one test

```python
# WRONG: four unrelated behaviors in one test
def test_compact_end_to_end(populated_db, tmp_path) -> None:
    target = tmp_path / "out.duckdb"
    result = compact_database(populated_db, target)
    assert result.target.exists()                                            # file io
    assert result.target_size < result.source_size                           # shrink
    conn = duckdb.connect(str(target), read_only=True)
    row = conn.execute("SELECT count(*) FROM items").fetchone()
    conn.close()
    assert row is not None and row[0] == 100                                 # data
    assert "compacted" in target.name                                        # naming

# RIGHT: one behavior per test, related assertions grouped
def test_compact_shrinks_bloated_db(bloated_db, tmp_path) -> None:
    result = compact_database(bloated_db, tmp_path / "out.duckdb")
    assert result.target_size < result.source_size
    assert result.delta_bytes < 0
    assert result.delta_pct < 0

def test_compact_roundtrips_data(populated_db, tmp_path) -> None: ...
```

## Parametrize over hand-rolled tables

Use `@pytest.mark.parametrize` for tables. Use `pytest.param(...)` with an `id=` when the case needs a descriptive name (regression marker, semantic label). Stack `@pytest.mark.parametrize` decorators only when the cartesian product is what you want; if you want only some combinations, build the case list explicitly.

```python
# RIGHT: parametrize with descriptive ids
@pytest.mark.parametrize(("value", "expected"), [
    (0, "0.0 B"),
    (1024, "1.0 KiB"),
    pytest.param(1024 ** 6, "1024.0 PiB", id="overflow_falls_off_table"),
])
def test_human_size(value, expected) -> None:
    assert human_size(value) == expected
```

The current `test_human_size.py` follows this shape.

## Naming and ordering

### Business-language names

Name after what the code does, not how.

```python
# WRONG
def test_pathlib_with_suffix_in_compact(): ...
def test_value_error_branch(): ...

# RIGHT
def test_compact_default_target_appends_dot_compacted(): ...
def test_compact_rejects_same_source_target(): ...
```

### Test discovery

pytest discovers tests via the file pattern `test_*.py` under `tests/`, with functions prefixed `test_`. The current layout uses snake_case file names that group by surface (`test_core.py`, `test_cli.py`, `test_extensions.py`, `test_rebuild.py`, `test_human_size.py`). Add new test functions to the file whose surface they exercise; create a new `test_<surface>.py` only when the surface is genuinely new.

### Order: happy → variation → config → edge → error

Within a file, order functions and parametrize cases happy → variation → config → edge → error. Soft convention; reorder only when adding new tests, not as a cleanup pass.

| Category | Indicators |
|---|---|
| Happy | No edge/error language in the name |
| Variation | `with`, `using`, `for` modifiers |
| Config | `mode`, `option`, `flag`, `setting` |
| Edge | `empty`, `null`, `zero`, `max`, `min`, `boundary` |
| Error | `rejects`, `fails`, `invalid`, `error`, `raises` |

### Execution time

If a test is noticeably slow, check for unintended external calls (network, real disk fsync), oversized fixtures, or unbounded iteration. The current suite completes in ~3 seconds; a test that takes seconds is a signal, not a feature. The committed `shopware-cli-chunks.duckdb` fixture exists because the rebuild needs a real ChunkHound shape — keep new large fixtures rare and document their provenance in `conftest.py` (see `shopware_cli_index`).
