# Surface Shapes (reference)

Three human-prose surfaces in scope here: root `README.md`, `docs/architecture.md`, and `docs/out-of-scope.md`. AGENTS.md is covered by `references/agents-md.md`; code comments live with `writing-python-code`.

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

**Job:** mechanism, not pitch. Why the implementation looks the way it does; what each step does; what each constant means. Per-case reasoning for refused / dropped / not-pursued items lives in `out-of-scope.md`; architecture.md's §Not supported section enumerates the refused cases briefly and points at `out-of-scope.md §<topic>` for each.

**Sections (in order):**

1. Why a custom rebuild (rationale: what alternatives fail and why)
2. Compaction pipeline (numbered steps; what each step does)
3. Internal data structures (recipe table schema, key constants)
4. Extension bundling (how `vss` is loaded; why bundled)
5. Not supported (and why) — brief enumeration of refused cases, each pointing at the matching `out-of-scope.md §<topic>` for full reasoning and fix shape

**No self-describing intro.** Do not open with "This document describes how X works. For Y see README." The H1 already names the topic. Cross-references attach inline to the paragraph that benefits from them, not to a meta-preface.

**No content the README already carries.** If a sentence appears in the README, architecture.md links to it via `(README §<heading>)` rather than restating. The README owns the pitch and quick-start; architecture.md owns the mechanism.

**No per-case reasoning out-of-scope.md owns.** §Not supported is the enumeration surface; the deep reasoning lives in out-of-scope.md. A bullet here is "Generated columns. (see out-of-scope.md §Generated columns)" not a paragraph restating why generated columns are refused.

## docs/out-of-scope.md

**Audience:** a maintainer answering "why don't we handle X?" or "what would it take to broaden scope to X?".

**Job:** per-topic catalog. Each refusal, drop, latent edge, or rejected approach gets one `##` section that owns both the why-not AND the fix shape (when one applies). One concept, one section, both aspects on the same surface.

**Section shape (each `##` heading):**

1. Why-not prose: one or two paragraphs naming the refusal mechanism (catalog function, regex, raise site) and the reason scope was not widened.
2. `**Fix shape.**` lead-in (optional): a numbered list of concrete steps to close the gap, followed by a regression-test sentence. Omit when no fix shape applies (structural property, UX decision); say so in one sentence instead.

**Structure is flat.** No `###`-level groupings of topics. Order conveys grouping: refused source shapes, then silently-dropped metadata, then latent code edges, then alternative approaches considered.

**Do not split a topic across files.** A separate "fix-shape" sibling doc creates triangular duplication: the topic appears on both surfaces with overlapping prose, and single-surface discipline does not survive normal edits. Both aspects of one topic stay in one section here.

**No self-describing intro.** Same rule as README and architecture.md. The H1 + the opening one-sentence frame are the whole intro.

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

The current "Not supported" section in architecture.md is a brief enumeration of refused cases with pointers into `out-of-scope.md` (where the per-case reasoning + fix shape live). If a new case is handled-but-degraded (the code does X partially, refuses Y) so the refused / dropped / considered framing in out-of-scope.md doesn't fit cleanly, promote to a §Contracts section using H/R/NC instead.
