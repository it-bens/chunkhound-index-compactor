# Contributing

## Setup

```bash
uv sync --extra dev      # Python toolchain (mypy, pytest, ruff, typos, pre-commit)
npm install              # Node toolchain (prettier; one-time per clone)
uv run pre-commit install  # wires the git pre-commit hook (one-time per clone)
```

Python 3.10 through 3.13 supported. macOS and Linux are tested.

The pre-commit hook runs ruff, typos, prettier, mypy, and pytest on every commit. Bypass with `git commit --no-verify` when you need to ship a WIP commit; CI still runs the same checks.

## Local checks

```bash
uv run pytest
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run typos
npx prettier --check .
```

Apply prettier fixes with `npm run format:fix`. CI runs the same commands.

### Tooling notes

- **Ruff**: `E W F I B C4 UP ARG SIM PTH`, line length 100, `E501` ignored.
- **MyPy**: strict; `tests.*` relaxed; `duckdb`, `duckdb_extension_vss` missing-imports ignored.
- **Pytest**: discovers `tests/`. Fixtures bundle real ChunkHound DB samples (see `tests/conftest.py`).
- **Typos**: config at `.typos.toml`.
- **Prettier**: config at `.prettierrc.json` and `.prettierignore`. Markdown is excluded because prettier mangles identifiers containing underscores and bloats markdown tables past the 100-column line limit.

## CI workflows

Four workflows under `.github/workflows/`. All third-party actions are SHA-pinned with `# vX.Y.Z` comments so Renovate can update them later.

### ci.yml

Runs on every push to `main` and every PR targeting `main`. Six parallel jobs:

| Job         | What                                                                                  |
|-------------|---------------------------------------------------------------------------------------|
| `lint`      | `ruff check` and `ruff format --check`                                                |
| `typecheck` | `mypy src/`                                                                           |
| `test`      | `pytest` with coverage on a Python 3.10 / 3.11 / 3.12 / 3.13 matrix (`ubuntu-latest`) |
| `typos`     | `typos` against the repo                                                              |
| `prettier`  | `prettier --check .`                                                                  |
| `build`     | `uv build`; uploads wheel + sdist as a `dist` artifact (14-day retention)             |

Coverage is reported but not gated. The `build` job runs independently of the others, so PR reviewers can download a wheel even when other jobs fail.

### test-action.yml

Runs on every push to `main` and every PR targeting `main`. Exercises the composite action via `uses: ./` on an `ubuntu-latest` / `macos-latest` matrix, proving it works with each runner's preinstalled Python. Each job compacts the committed ChunkHound fixture twice: once with `skip-hnsw` (RAM-flat, asserts the output is smaller) and once with a full HNSW rebuild (asserts the default target is written).

### rolling.yml

After `ci.yml` succeeds on `main`, deletes the existing `rolling` tag plus release and recreates them at the new HEAD SHA. Marked prerelease and not-latest so it never shadows a tagged release.

Install the current main-branch build from the rolling asset:

```bash
uv tool install https://github.com/it-bens/chunkhound-index-compactor/releases/download/rolling/chunkhound_index_compactor-<version>-py3-none-any.whl
```

### release.yml

Triggers on a `v*.*.*` tag push or manual `workflow_dispatch` against a tag ref.

1. Verifies the ref is a tag.
2. Verifies the tag (minus the `v` prefix) matches `pyproject.toml`'s `version`.
3. Builds wheel + sdist.
4. Publishes to PyPI via Trusted Publisher (OIDC; no PyPI token in the repo).
5. Creates a GitHub Release with the wheel + sdist attached. Release notes are not auto-generated; edit them on GitHub afterward.

## Release process

To cut a release:

1. Bump `version` in `pyproject.toml` (for example, `0.1.0` to `0.2.0`) and update `CHANGELOG.md`.
2. Commit, push, wait for CI to pass on `main`.
3. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
4. The release workflow publishes to PyPI and creates a GitHub Release.
5. Open the Release on GitHub and write the release notes.

To re-trigger from a tag manually (for example, after a transient PyPI failure): GitHub Actions, then Release, then Run workflow, then pick the tag from the ref dropdown.
