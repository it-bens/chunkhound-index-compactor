# Code Comments and Docstrings (reference)

## Classification table

| Bucket | Action |
|---|---|
| **Redundant with architecture.md** (70%+ wording overlap with an architecture section) | Remove or compress to one line |
| **Explains-what** (paraphrases the identifier or the code below) | Remove |
| **Tutorial / novice-facing** (narrates stdlib or flow) | Remove or compress sharply |
| **Over-specified why** (five lines where one would do) | Tighten |
| **Load-bearing why** (names the failure mode the code guards against) | **Keep verbatim**; rewrite new why-comments toward this shape |
| **Public docstring** (PEP 257 for an exported function / class / dataclass) | Keep when load-bearing; compress pure paraphrase to one line, never remove from the public surface |

## When to write a docstring vs a `#` comment

- **Public symbol (exported, no leading underscore):** PEP 257 docstring. One-line if behavior is obvious from the signature plus identifier; multi-line only when the function carries non-obvious raise behavior, ordering constraint, or side effect.
- **Private helper (`_`-prefixed):** no docstring unless the helper carries a load-bearing why. A `#` comment above the body is fine when the why is point-of-use.
- **Inline why inside a function body:** `#` comment, one to four lines, sitting directly above the line it explains.

The current `core.py` shows the pattern: `compact_database` carries a multi-line docstring (raises behavior + skip_hnsw side effect are not obvious from the signature), `_reject_unsupported_objects` carries a docstring because the catalog-flag subtlety is load-bearing, `_escape_sql_literal` carries none because the body is one line and the name is the contract.

## Load-bearing why: worked example

A load-bearing why-comment names the failure mode, gives a concrete instance, and shows what the code does to prevent it. Compare against this shape when writing or keeping a why-comment:

```python
def _reject_unsupported_objects(conn: duckdb.DuckDBPyConnection) -> None:
    """Fail hard if the attached `src` has non-main schemas or any view.

    `duckdb_schemas()` marks the source's own `main` as `internal = true`, so
    detect non-main objects via the tables/views catalogs filtered on
    `schema_name`, not on the `internal` flag.
    """
    # ...
```

The docstring names the failure (filtering on `internal` would drop the source's own `main`), gives the concrete catalog fact that produces it, and identifies the alternative (filter on `schema_name`). None of those facts is recoverable from the code alone.

A second shape is the *negative* invariant comment: justify why an obvious-looking guard is deliberately omitted. Example: `_escape_sql_literal` only doubles single quotes; if someone "improves" it to also escape double quotes, identifier-quoted names break. A one-line comment explaining "DuckDB DDL accepts double-quoted identifiers; only literal-text single quotes need doubling" would prevent that regression. Without that comment, the next editor will "fix" the missing escape and break correctness.

## Banned patterns

```
WRONG:   def compact_database(
             source: Path, target: Path, *, skip_hnsw: bool = False
         ) -> CompactionResult:
             """Compact a DuckDB database.

             Opens the source, reads the schema, computes a topological order,
             attaches the target, replays sequences and tables, inserts rows,
             replays indexes, checkpoints, detaches, closes, returns a result.
             """
CORRECT: def compact_database(
             source: Path, target: Path, *, skip_hnsw: bool = False
         ) -> CompactionResult:
             """Rebuild `source` DuckDB database into a fresh file at `target`.

             Raises:
                 ValueError: ... (the parts that are not obvious from the body)
                 FileNotFoundError: ...
                 FileExistsError: ...
             """
```

The WRONG version narrates the function body. The body already says what the function does. The docstring's job is the contract a caller cannot infer.

```
WRONG:   # See README §Compaction pipeline
         conn.execute(...)
CORRECT: conn.execute(...)
```

The pipeline is documented in architecture.md, not the README. The cross-reference inside a code comment dilutes the link's value and rots when the section renames. Architecture.md is the authoritative narrative; the code is the authoritative behavior. Pointing from code to docs gets the direction wrong.

```
WRONG:   # see src/chunkhound_index_compactor/core.py:362
CORRECT: # see src/chunkhound_index_compactor/core.py §_escape_sql_literal
```

Line numbers shift the moment anyone reformats. Section/symbol names survive heading-internal edits.

```
WRONG:   # increment counter by one
         i += 1
CORRECT: i += 1
```

The "explains-what" pattern. The code is shorter than the comment.

## Type hints carry contract too

A complete type signature replaces several lines of docstring. `def f(p: Path, *, skip: bool = False) -> CompactionResult:` already states "first arg is a path, skip is keyword-only and defaults to False, return is a CompactionResult". The docstring only needs to add what the signature can't say (raised exceptions, side effects, ordering constraints).

`from __future__ import annotations` is required at the top of every file. With it, `dict[str, str]`, `list[T]`, and `X | None` work on the project's 3.10 floor (the annotations are strings until something reflects on them).
