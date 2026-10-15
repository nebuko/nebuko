---
description: "Complexity tier rubric and the `complexity_review` re-tier request contract for this repo."
---

# Complexity Governance Skill

Complexity is governed **per repo** by a layered policy (`builtin` -> `repo`) and
adjudicated by the `ComplexityGovernor`. Base tier resolution is unchanged
(`dispatch > step > "medium"`, ADR-0005); the governor only overlays a clamped
adjustment - `effective = clamp(base + delta)` with `|delta| <= max_auto_delta`
(default 1).

This skill is attached to the agent roles that participate in complexity
adjudication. It is the single source of truth for the tier rubric and the
re-tier request contract; do not restate it in role prompts or other skills.

## Tier guidance

- **LOW** - localized, single-concern change: one file or a tight cluster, no
  design ambiguity, no cross-module contract change, no open questions. If it
  fits in a single focused edit, it is LOW.
- **MEDIUM** - the default working tier: a bounded multi-file change with a clear
  approach, a couple of touch points, no unresolved design choice. Most feature
  work and bug fixes belong here.
- **HIGH** - reserved for genuinely heavy work: multiple linked issues or a
  reopened problem, cross-module/architectural change, sustained large context
  burn, or repeated human change-requests. Reach for HIGH only when the evidence
  below actually fires; an unproven HIGH erodes the cost/value of the tier.

## Drift signals

Automatic drift signals span all three evidence families (LLM + human + context).
Context signals are supplied by the intake surface via `COMPLEXITY_CONTEXT_SIGNALS`;
no publisher writes that var yet, so today only the LLM + human rules fire:

| signal | family | field | threshold |
|---|---|---|---|
| `multi-issue-linkage` | context | `linked_issues` | >= 2 |
| `reopened-issue` | context | `reopened` | true |
| `high-token-burn` | llm | `input_tokens` | >= 150000 |
| `large-scope` | llm | `nb_tool_calls` | >= 40 |
| `human-friction` | human | `changes_requested` | >= 2 |

## Requesting a re-tier

If a step is genuinely mis-tiered, call the `complexity_review` tool with a
**mandatory** `evidence` bundle (at least one of `llm` / `human` / `context`
populated) and a **mandatory non-blank** `feeling` (loop detected / weird design
/ strange signal). `suggested_tier` is advisory input, **not** a command - the
governor clamps it. The resulting immutable `ComplexityAdjustment` is recorded on
the next history entry and applied to the following step's `CONFIG_COMPLEXITY`.
