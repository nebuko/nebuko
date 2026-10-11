# Calling Another Agent

Use this to hand off to a specific agent of your choice. This is an **imperative** signal: you decide who runs next, overriding workflow continuation.

## When to use which signal — one-line rule

- `dispatch` = how **this run ends** inside a workflow (the engine decides the next step).
- `function_call` = **imperative hand-off** to a specific agent, outside or over the workflow.

Never use `@@agent` mentions to call another agent: bot messages are ignored for `@@` tokens (issue #478), and stray `@@` in prose can mis-trigger CI runs.

## Format

End your message with a single `AGENT_RUN_CONFIG` sentinel. Put the `function_call` section next to `dispatch` (both optional, but include at most one `function_call` per message):

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "function_call": {"function": "call_agent", "agent_name": "context", "complexity": "medium"}} -->
```

Rules:

- **One function call per message.** If several appear, the last valid one wins — but do not rely on that; emit exactly one.
- `function` must be a registered name. Currently only `call_agent` is registered.
- `agent_name` must be a known agent roster name: `architect`, `context`, `dev`, `review`, `po`.
- `complexity` (optional, default `"medium"`): `low`, `medium`, or `high`.
- `callback` (optional): the **return address** — the agent to run once the callee finishes without a decision of its own. It defaults to the **emitting caller**, so you usually omit it; set it explicitly only to route the return somewhere other than yourself. A `callback` equal to `agent_name` is a no-op.
- The call is **one-shot**: it is consumed by the next run and cleared from the workflow state, so it can never re-fire.
- **Keep the sentinel on a single line, inside the code fence shown above only.** Do not break the JSON across lines or wrap it in extra markdown fences — extraction is resilient to drift (issue #512), but a clean single-line sentinel is the safest form.

## Return path and precedence (issue #765)

When a callee finishes, the engine resolves its successor in this order (highest → lowest):

1. The callee's own explicit `function_call` → route there.
2. The callee's own explicit `dispatch` `hold` / `loop` / `terminate` → honor it.
3. The registered `callback` → return to the caller.
4. The workflow forward edge (`steps[].next`) → continue the workflow.
5. No target at all → **default `hold`** (the no-action safety net; never auto-advance an unknown state).

`dispatch advance` means "I am done — route me by the standard rules above". The callback (step 3) wins over the workflow edge (step 4) because it is explicit caller intent, but the callee's own action (steps 1–2) always wins over both.

## Notes

- An explicit `call_agent` beats workflow continuation — same priority tier as a human mention. Use it deliberately, not as a generic "next" mechanism.
- If you are inside a defined workflow and simply finishing the current step, use `dispatch` alone — let the workflow route the next step.
- Self-calls (`agent_name` = yourself) are blocked by the loop guard; do not emit them.

## Complexity Governance (issue #882)

Complexity is governed **per repo** by a layered policy (`builtin` -> `repo`) and
adjudicated by the `ComplexityGovernor`. Base tier resolution is unchanged
(`dispatch > step > "medium"`, ADR-0005); the governor only overlays a clamped
adjustment - `effective = clamp(base + delta)` with `|delta| <= max_auto_delta` (default 1).

Tier guidance (mirrored verbatim from the builtin policy bands):

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

Automatic drift signals span all three evidence families (LLM + human + context):

| signal | family | field | threshold |
|---|---|---|---|
| `multi-issue-linkage` | context | `linked_issues` | >= 2 |
| `reopened-issue` | context | `reopened` | true |
| `high-token-burn` | llm | `input_tokens` | >= 150000 |
| `large-scope` | llm | `nb_tool_calls` | >= 40 |
| `human-friction` | human | `changes_requested` | >= 2 |

**Requesting a re-tier.** If a step is genuinely mis-tiered, call the
`complexity_review` tool with a **mandatory** `evidence` bundle (at least one of
`llm` / `human` / `context` populated) and a **mandatory non-blank** `feeling`
(loop detected / weird design / strange signal). `suggested_tier` is advisory
input, **not** a command - the governor clamps it. The resulting immutable
`ComplexityAdjustment` is recorded on the next history entry and applied to the
following step's `CONFIG_COMPLEXITY`.
