# pydoc Consultation (reference)

## Query shapes — pick the narrowest that answers the question

| Need | Command | Typical size |
|---|---|---|
| One function, class, method, or constant | `python -m pydoc <pkg>.<Symbol>` or `python -m pydoc <pkg>.<Class>.<method>` | small |
| Class with its methods | `python -m pydoc <pkg>.<Class>` | medium |
| Module overview (top-level functions, classes, constants) | `python -m pydoc <pkg>` | medium |
| Keyword search across installed modules | `python -m pydoc -k <keyword>` | varies |
| Full HTML dump (browse offline) | `python -m pydoc -w <pkg>` | large; avoid for one-off lookups |

`pydoc` reads the installed package's `__doc__` strings and signatures. For dependencies the project pins (`duckdb`, `duckdb_extension_vss`, `typer`), `uv run python -m pydoc duckdb.DuckDBPyConnection` works because `uv` activates the project's virtualenv automatically.

When the docstring is thin (true of most C-extension wrappers like `duckdb`), escalate to reading the source: `uv run python -c "import duckdb; print(duckdb.__file__)"` and open the file, or `uv run python -m pydoc -k <keyword>` to find adjacent symbols. For `duckdb`'s SQL behavior, the authoritative reference is the DuckDB website, but `pydoc duckdb.DuckDBPyConnection.execute` confirms the Python signature (what `execute` accepts for `parameters`, what `fetchall()` returns).

## Inspecting at runtime when docs are insufficient

Some library behaviors are only documented in the source. Two safe ways to look:

```bash
uv run python -c "import duckdb; help(duckdb.DuckDBPyConnection.execute)"
uv run python -c "import inspect, duckdb; print(inspect.signature(duckdb.connect))"
```

`help()` paginates the same content as `pydoc` but inside the REPL. `inspect.signature` returns the parameter shape without rendering the docstring.

## Banned patterns

```
WRONG:   (write the call, then fix exceptions iteratively at runtime)
CORRECT: uv run python -m pydoc pathlib.Path.with_suffix   # before the call

WRONG:   uv run python -m pydoc -w duckdb   # 5+ MB HTML dump for one method
CORRECT: uv run python -m pydoc duckdb.DuckDBPyConnection.execute

WRONG:   guess from the type name and skip verification when the method has
         a footgun-prone signature (overloaded varargs, positional-vs-keyword
         differences across versions)
CORRECT: pydoc the exact symbol; check the version pin in pyproject.toml
         when the docstring references behavior that changed between releases
```

## When consultation is not required

- The call already runs and a test exercising it passes — don't retroactively look up working code
- Mechanically repeating an idiom already established elsewhere in the same file
- Built-ins (`len`, `range`, `enumerate`, `zip`, `isinstance`, `print`, `dict`, `list`, `tuple`, `set`, `str`, `int`, `float`)
- Standard library symbols that are in everyday use (`pathlib.Path(...)`, `re.compile(...)`, `dataclass`) — but `pydoc` on the specific method you're about to call is still cheap and prevents subtle mistakes (e.g. `Path.with_suffix` requires the suffix to start with `.`)

## When to escalate past pydoc

`pydoc` is the cheap first lookup. Escalate when:

- The docstring is empty or a one-line stub (common for `duckdb`'s C-extension methods) → read the project source or the upstream documentation.
- The signature accepts `**kwargs` and the accepted keys are not listed → grep the source for the kwarg use, or check the upstream changelog for the version your `pyproject.toml` pins.
- The method exists on multiple types (`execute` on `Connection` and on `DuckDBPyRelation`, with different semantics) → confirm which type you have in scope before calling.
