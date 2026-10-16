---
description: "Hand off to a specific next agent with `call_agent`, overriding the default workflow continuation."
---

# Calling Another Agent

Use this to hand off to a specific agent of your choice. This is an **imperative** signal: you decide who runs next, overriding workflow continuation.

## When to use which signal — one-line rule

- `dispatch` = how **this run ends** inside a workflow (the engine decides the next step).
- `function_call` = **imperative hand-off** to a specific agent, outside or over the workflow.

Never use `@@agent` mentions to call another agent: bot messages are ignored for `@@` tokens (issue #478), and stray `@@` in prose can mis-trigger CI runs.

## Format

Use the typed `call_agent` control-plane tool — it records the hand-off structurally, immune to long-context format drops.

Hand-off fields (do not write a sentinel yourself — the tool records the hand-off structurally):

- **One function call per message.** If several are given, the last valid one wins — but do not rely on that; supply exactly one.
- `function` must be a registered name. Currently only `call_agent` is registered.
- `agent_name` must be a known agent roster name: `architect`, `context`, `dev`, `review`, `po`.
- `complexity` (optional, default `"medium"`): `low`, `medium`, or `high`.
- `callback` (optional): the **return address** — the agent to run once the callee finishes without a decision of its own. It defaults to the **emitting caller**, so you usually omit it; set it explicitly only to route the return somewhere other than yourself. A `callback` equal to `agent_name` is a no-op.
- The call is **one-shot**: it is consumed by the next run and cleared from the workflow state, so it can never re-fire.

## Return path and precedence (issue #765)

When a callee finishes, the engine resolves its successor in this order (highest → lowest):

1. The callee's own explicit `function_call` → route there.
2. The callee's own `hold` verdict (`dispatch_next(appreciation='hold')`) → honor it; a deliberate stop is never redirected.
3. The registered `callback` → return to the caller.
4. The workflow forward edge (`steps[].next`) → continue the workflow.
5. No target at all → the run records `INCOMPLETE`/`NO_OUTCOME` (the no-action safety net; never auto-advance an unknown state).

`dispatch_next(appreciation='approved')` means "I am done — route me by the standard rules above". The callback (step 3) wins over the workflow edge (step 4) because it is explicit caller intent, but the callee's own action (steps 1–2) always wins over both.

## Notes

- An explicit `call_agent` beats workflow continuation — same priority tier as a human mention. Use it deliberately, not as a generic "next" mechanism.
- If you are inside a defined workflow and simply finishing the current step, use `dispatch` alone — let the workflow route the next step.
- Self-calls (`agent_name` = yourself) are blocked by the loop guard; do not emit them.
