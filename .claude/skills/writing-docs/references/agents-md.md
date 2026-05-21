# AGENTS.md (reference)

AGENTS.md is a pointer-only map into adjacent human prose surfaces (root README, docs/architecture.md, docs/out-of-scope.md). It is consumed by LLM coding tools (Claude Code, Codex, similar) and is opaque to humans by policy. No human-facing pitch, no welcome copy, no narrative motivation. Every bullet is a pointer `(README §X)` / `(architecture.md §Y)` / `(out-of-scope.md §Z)` or it gets deleted. Hard ceiling: 30 lines outside section headers, 3 to 8 bullets per section.

## LLM-only discipline

The file's audience is the tool that auto-loads it on session start. A reader who is not that tool should bounce off. Banned content:

- Project pitch / "What this does" narrative
- Quick-start commands, install steps, CLI examples (those belong in README)
- Step-by-step explanation of the pipeline (architecture.md owns that)
- Emoji-decorated section headers
- Motivation sentences attached to bullets
- "For details, see X" prefaces in the body — the bullet ends with the pointer, no preamble

## Skeleton

```markdown
# AGENTS.md — chunkhound-index-compactor

<one short line of scope, optional, ≤15 words>

## Layout

```
<directory tree with one-line role per node>
```

## Module → symbols

| Module | Public | Private |
|---|---|---|
| ... | ... | ... |

## When to modify

| Task | File / symbol |
|---|---|
| ... | ... |

## Invariants enforced by code

- <one-line invariant> (architecture.md §<heading>)
- ...

## Build / verify

<commands the agent runs to validate>
```

The `## Layout` / `## Module → symbols` / `## When to modify` sections carry navigation. The `## Invariants` section carries warnings (the bullets that change behavior on edit). `## Build / verify` carries the verification commands.

## Bullet discipline (`## Invariants enforced by code`)

Every bullet ends with `(README §<heading>)`, `(architecture.md §<heading>)`, or `(out-of-scope.md §<heading>)` pointing at a real heading. The bullet states the rule; the linked surface carries the *why*.

- **No motivation in the bullet.** Motivation lives in README or architecture.md. AGENTS.md only points at it.
- **Front-load by stakes.** The first bullet under `## Invariants enforced by code` carries the most attention weight. Order by stakes, not by source-file order.
- **Cross-refs use `§<heading name>`, never line numbers, never anchor links.** Headings survive edits; line numbers don't.
- **3 to 8 bullets per section.** Twelve is a smell; prose drift is the failure mode the 30-line ceiling prevents.
- **Identifiers mirror the code.** Don't shorten `compact_database` to `compact` to tighten the bullet — abbreviations that drift from the identifiers defeat grep and break the pointer-to-prose coupling.

## Worked WRONG / CORRECT

```
WRONG:   - The recipe table is named `_compactor_hnsw_recipe`; it carries
           the metric, column, table, and index name so that restore_indexes
           can rebuild the stripped HNSW indexes idempotently after
           --skip-hnsw is used to skip RAM-heavy index building.
CORRECT: - --skip-hnsw output carries a `_compactor_hnsw_recipe` table.
           (architecture.md §The `_compactor_hnsw_recipe` table)
```

The WRONG version explains the *why* (RAM-heavy index building, idempotent restore). That belongs in architecture.md. The bullet's job is to flag the rule and point.

```
WRONG:   - SQL literal escape: see src/chunkhound_index_compactor/core.py:362
CORRECT: - Wrap every SQL string literal in `_escape_sql_literal` before
           interpolation. (architecture.md §Compaction pipeline)
```

Line numbers shift the moment anyone reformats the file. Section names survive heading-internal edits and only break on a rename — at which point the cross-ref integrity gate in SKILL.md catches the rename and forces the sweep.

## Decision Test (per bullet)

Three questions, one bullet at a time:

1. **Shape.** Does the bullet end with `(README §<heading>)`, `(architecture.md §<heading>)`, or `(out-of-scope.md §<heading>)` pointing at a real heading, and avoid explaining *why* in the bullet itself?
2. **Provenance.** Can the rule trace to a specific incident, a recurring class of bug the project has corrected, or a load-bearing test invariant? "We might want this someday" is not provenance.
3. **Visibility.** If the rule were violated tomorrow, would the failure be visible — a test breaks, a guarantee voids, a contract refuses — or invisible (a stylistic preference)? Invisible rules are noise.

Outcomes:

- All three pass → proceed.
- No pointer → delete the bullet, or rewrite as a pointer.
- Explains motivation → move the motivation to README, architecture.md, or out-of-scope.md; leave only the rule + pointer.
- Pointer targets a vague or missing heading → fix the heading first (heading-predicts-content discipline), then repoint.
- No traceable provenance → do not add the rule. Wait for evidence to surface.
- Violation would be invisible → drop the rule; AGENTS.md is for warnings, not preferences.

## CLAUDE.md companion

`CLAUDE.md` at the project root contains the single line `@AGENTS.md`. CLAUDE.md is not authored prose; it is a one-line include directive that Claude Code resolves on session start. When AGENTS.md is created, CLAUDE.md is created adjacent. When AGENTS.md is deleted, CLAUDE.md is deleted.

## Common rationalizations to refuse

| Thought | Reality |
|---|---|
| "I'll add a one-line summary so readers don't need the README" | The summary will drift. The pointer is the discipline. |
| "An intro paragraph helps the agent orient" | The agent does not need orientation prose. The H1 + section headings are the orientation. |
| "Every entry needs the *why*" | The *why* lives in README / architecture.md. AGENTS.md only points. |
| "I'll shorten the identifier to tighten the bullet" | Abbreviations that drift from the identifiers defeat grep. |
| "Markdown emojis make sections friendlier" | AGENTS.md has no human readers to befriend. Decoration is noise. |
