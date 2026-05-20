# Behavior Under Test (reference)

## Behavior, not implementation, trivial, or internal

Tests verify observable behavior of the public API.

### Do test

- Return values and raised exceptions
- Observable state changes (files written on disk, rows persisted in a DuckDB table, output streams flushed)
- Computed or derived values
- Validation logic (the `ValueError`, `FileNotFoundError`, `FileExistsError` paths in `core.py`)

### Do NOT test

- Calls into dependencies. The return value already verifies the call happened.
- Underscore-prefixed helpers via a dedicated test when the public API covers the behavior. The current `_topological_order` tests are an exception — see *Carve-outs* below.
- Internal call order or algorithmic decomposition.
- Logic-free dataclasses (`CompactionResult.source = path; assert result.source == path`). Test when the dataclass computes a derived value (the existing `delta_pct` test is correct).
- `assert isinstance(result, CompactionResult)` on a function whose return type is `CompactionResult`. Trivially true; delete it or replace with a behavior assertion that uses the dataclass fields.
- Pure delegation: a function that forwards to a dependency without transforming input or output; test when delegation transforms input/output or includes conditional logic.

### Carve-outs

- `with pytest.raises(...)` before the act step is fine — `pytest.raises` *is* the act for an error-path test. The two-phase shape (arrange → expect + act inside the `with` block) is idiomatic.
- Testing `_topological_order` directly is acceptable because the behavior under test is the FK-ordering contract that drives the rebuild, and the function is the smallest unit that exhibits it. The current tests (`test_topological_order_sorts_parent_before_child`, `test_topological_order_keeps_all_independent_tables`, `test_topological_order_rejects_cycle`) sit just below the public API because the FK ordering is the load-bearing invariant — the cycle-rejection path raises only here.
- Drift-guard tests that iterate two registries and assert index-alignment or set-equality are valid behavior tests of the registry contract. The behavior under test is "registry A and registry B stay in sync as entries land." Assert via the public surface.

### Worked examples

```python
# WRONG: dataclass round-trip
def test_compaction_result_holds_fields() -> None:
    r = CompactionResult(source=Path("a"), target=Path("b"), source_size=1, target_size=2)
    assert r.source == Path("a")
    assert r.target == Path("b")

# WRONG: pure delegation
def test_cli_compact_calls_compact_database(monkeypatch) -> None:
    called = {}
    def fake(*_args: object, **_kwargs: object) -> None:
        called["hit"] = True
    monkeypatch.setattr("chunkhound_index_compactor.cli.compact_database", fake)
    runner.invoke(app, ["src.duckdb"])
    assert called["hit"]   # the return value already proves the call happened

# RIGHT: validation
def test_compact_rejects_views(tmp_path: Path) -> None:
    src = tmp_path / "views.duckdb"
    # ... create a view
    with pytest.raises(ValueError, match="view"):
        compact_database(src, tmp_path / "out.duckdb")

# RIGHT: derived behavior
def test_delta_pct_negative_when_target_smaller() -> None:
    r = CompactionResult(source=Path("a"), target=Path("b"), source_size=200, target_size=100)
    assert r.delta_pct == -50.0
```

### Seam introduction patterns when behavior is not observable

When the behavior is real but the current public API hides it, four production-code seams have surfaced as legitimate paths to observability. Each survives in production for reasons unrelated to the test — real injection points the production caller already wants. Choose the seam that matches what production wants; if none fits without contorting production code, the behavior is implementation detail and the test should be reframed or deleted instead.

| Pattern | Production-code shape | Test usage |
|---|---|---|
| Keyword `out: TextIO` parameter | Function takes `*, out: TextIO = sys.stdout`; the CLI default flows through. | Test passes `out=io.StringIO()` and asserts on `.getvalue()`. |
| `monkeypatch.setattr` on a module-level singleton | Production code reads through `from .config import settings`; the constant is overridable. | Test `monkeypatch.setattr("pkg.config.settings", FakeSettings(...))`; auto-restored on teardown. |
| Exported pure helper | Logic that the production caller and the test both invoke is extracted into a top-level helper (e.g. `_topological_order`). | Test exercises the helper directly without staging the surrounding pipeline. |
| Protocol-typed constructor parameter | `class Service: def __init__(self, *, store: Storage = DiskStorage()): ...` where `Storage` is a `typing.Protocol`. | Test instantiates `Service(store=FakeStorage())`; no monkeypatching. |

Anti-pattern: introducing a keyword-only parameter that no production caller actually passes, only to make the test pass. That is the same "test the private helper" smell, dressed in a parameter.

## Single behavior per test

Each test function exercises exactly one behavior. Violation signs:

- Name contains *and* (`test_compact_and_restore_roundtrip`)
- Comment banners splitting the body into phases (`# create`, `# act`, `# delete`)
- Multiple unrelated assertions after distinct act steps

```python
# WRONG: test_compact_lifecycle exercises three behaviors
def test_compact_lifecycle(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"

    # compact
    result = compact_database(populated_db, target)
    assert result.target_size > 0

    # restore
    out = restore_indexes(target)   # raises; this is a populated_db, no recipe table

    # replace
    backup = replace_with_compacted(populated_db, target)
    assert backup.exists()
```

Split into `test_compact_returns_result`, `test_restore_without_recipe_fails_hard`, `test_replace_creates_backup_and_swaps`. Each test fails for one reason.

## Test redundancy

Every parametrize case and every top-level test covers a unique code path, boundary value, or regression. Key on *why* the case exists, not on *what* the input looks like.

A case earns its slot if at least one holds:

- **Unique code path**: triggers a branch no other case triggers
- **Boundary value**: exact threshold where behavior changes
- **Regression**: prevents a specific bug from returning; cite the issue or commit

If none hold, merge into an existing test with extra assertions, or delete.

### Preservation check

Before flagging a case as redundant, scan for preservation indicators:

| Indicator | Pattern |
|---|---|
| Regression marker in id | `regression`, `bug`, `issue`, `#\d+` |
| Issue tracker reference in id | `GH-`, `PR-`, commit SHA |
| Comment at site | `# regression for #123`, `# prevents the ...` |
| Parametrize id key | `"unicode_fix_#123"` |

If present, keep the case and add an explanatory comment. If absent, consolidate.

```python
# WRONG: all three cases exercise the same delta_pct < 0 branch
@pytest.mark.parametrize(("source_size", "target_size"), [
    (100, 1),
    (10, 1),
    (2, 1),
])
def test_delta_pct_negative(source_size, target_size):
    r = CompactionResult(source=Path("a"), target=Path("b"),
                         source_size=source_size, target_size=target_size)
    assert r.delta_pct < 0

# RIGHT: each case justifies itself by a distinct branch or boundary
@pytest.mark.parametrize(("source_size", "target_size", "expected"), [
    (100, 50, -50.0),   # negative delta path
    (100, 100, 0.0),    # equal-size boundary
    (100, 200, 100.0),  # positive delta path
    (0, 0, 0.0),        # zero-source guard (delta_pct returns 0.0)
])
def test_delta_pct(source_size, target_size, expected):
    r = CompactionResult(source=Path("a"), target=Path("b"),
                         source_size=source_size, target_size=target_size)
    assert r.delta_pct == expected
```

## Guard clause isolation

When a test targets one early-return in a function with multiple sequential guards, the arrange section satisfies every other guard so the tested guard is the only possible exit. Otherwise the test may pass because a different guard fired first; the outcome looks right and the test proves nothing.

1. Read the public function the test exercises.
2. Enumerate its sequential guard clauses.
3. If the function has 2+ guards and the test targets one, verify the arrange satisfies all others.
4. If another guard would short-circuit with the current arrange, flag.

Does not apply when: function has one guard; test explicitly covers the all-preconditions-absent path; guards produce distinguishable outcomes that the assertion discriminates.

```python
# compact_database has guards:
#   g1: target.resolve() == source.resolve() → ValueError("differ from source")
#   g2: not source.is_file()                 → FileNotFoundError(...)
#   g3: target.exists()                      → FileExistsError(...)

# WRONG: targets g3 but g2 fires first
def test_compact_target_exists_raises(tmp_path: Path) -> None:
    src = tmp_path / "src.duckdb"        # never created — g2 will fire
    tgt = tmp_path / "out.duckdb"
    tgt.write_bytes(b"")
    with pytest.raises(FileExistsError):
        compact_database(src, tgt)        # actually raises FileNotFoundError

# RIGHT: g1 and g2 satisfied; only g3 can fire
def test_compact_target_exists_raises(populated_db: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.duckdb"
    target.write_bytes(b"")
    with pytest.raises(FileExistsError, match="target already exists"):
        compact_database(populated_db, target)
```

The existing `test_compact_target_exists_raises` already uses `populated_db` and gets this right; the worked example is for review of new tests.
