# Surface Shapes (reference)

Two human-prose surfaces in scope here: root `README.md` and `docs/architecture.md`. AGENTS.md is covered by `references/agents-md.md`; code comments live with `writing-python-code`.

## Root README

**Audience:** an experienced Python developer evaluating the tool for task-fit and learning how to use it.

**Sections (in order):**

1. Title + one-paragraph pitch (motivating use case in one or two sentences; what the tool does; what it works on)
2. Quick Start — minimal install + run for the default and most common variants
3. CLI Usage — argument/option table + help-text excerpt
4. Library Usage — Python import example + raised-exception list per public function
5. Not Supported — terse list of refused inputs; link to architecture.md for the reasoning
6. Development — `uv sync --extra dev`, `pytest`, `ruff`, `mypy` invocations
7. License — single line + link to `LICENSE`

**Delegate, don't expand.** Deeper structure delegates to `docs/architecture.md`. The README does not restate the pipeline or the recipe-table schema; it points at architecture.md and stops.

**No self-describing intro.** The README does not open with "This document covers the X tool". The H1 already names the topic. The first sentence carries the pitch (what it does + motivating use case).

## docs/architecture.md

**Audience:** a reader who has already read the README and is now investigating internals.

**Job:** mechanism, not pitch. Why the implementation looks the way it does; what each step does; what each constant means; why each not-supported case is rejected.

**Sections (in order):**

1. Why a custom rebuild (rationale: what alternatives fail and why)
2. Compaction pipeline (numbered steps; what each step does)
3. Internal data structures (recipe table schema, key constants)
4. Extension bundling (how `vss` is loaded; why bundled)
5. Not supported (and why) — one bullet per refused case, with the reasoning that earned the refusal

**No self-describing intro.** Do not open with "This document describes how X works. For Y see README." The H1 already names the topic. Cross-references attach inline to the paragraph that benefits from them, not to a meta-preface.

**No content the README already carries.** If a sentence appears in the README, architecture.md links to it via `(README §<heading>)` rather than restating. The README owns the pitch and quick-start; architecture.md owns the mechanism.

## §Contracts H/R/NC pattern (when an invariant emerges)

The project is small enough today that the README and architecture.md cover the surface without formal contract sections. If an invariant emerges that the code actively enforces (a refused input, a documented residual risk), promote it to a `## Contracts` section under architecture.md using the **Handled / Refused / Not covered** skeleton verbatim:

```markdown
## Contracts

### {Invariant name}

**Handled.** {What the module does to satisfy the invariant. One sentence per case the implementation covers.}

- {Case 1.}
- {Case 2.}

**Refused.** {What the module rejects rather than handle. The refusal is part of the contract.}

- {Refused case 1, with the error or exception the caller sees.}

**Not covered.** {What the module does not address. The residual risk the caller carries.}

- {Uncovered case 1, with the consequence if the caller hits it.}
```

Every invariant has rows in all three buckets, even if a bucket has only "none" — explicit "none" is a contract, an absent bucket is a gap.

### §Limitations anti-pattern

A §Limitations heading is a §Contracts entry in disguise whenever the named constraint is actively enforced by code. The signal: a paragraph that says "the tool does not handle X" right next to a function that detects, refuses, or rewrites X. The detection is the contract; "limitation" is the wrong frame.

The current "Not supported" section in architecture.md sits at the boundary: the rules are listed with their reasoning, and the code currently enforces each one with `ValueError`. If the list grows beyond three cases or starts carrying handled-but-degraded entries, promote to a §Contracts section using H/R/NC.
