# Calling Another Agent

Use this to hand off to a specific agent of your choice. This is an **imperative** signal: you decide who runs next, overriding workflow continuation.

## When to use which signal — one-line rule

- `dispatch` = how **this run ends** inside a workflow (the engine decides the next step).
- `function_call` = **imperative hand-off** to a specific agent, outside or over the workflow.

Never use `@@agent` mentions to call another agent: bot messages are ignored for `@@` tokens (issue #478), and stray `@@` in prose can mis-trigger CI runs.

## Format

End your message with a single `AGENT_RUN_CONFIG` sentinel. Put the `function_call` section next to `dispatch` (both optional, but include at most one `function_call` per message):

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "function_call": {"function": "call_agent", "agent_name": "ctx", "complexity": "medium"}} -->
```

Rules:

- **One function call per message.** If several appear, the last valid one wins — but do not rely on that; emit exactly one.
- `function` must be a registered name. Currently only `call_agent` is registered.
- `agent_name` must be a known agent roster name: `genius`, `ctx`, `dev`, `review`, `issue`, `issue-reviewer`, `summarize`, `debug`, `po`.
- `complexity` (optional, default `"medium"`): `low`, `medium`, or `high`.
- The call is **one-shot**: it is consumed by the next run and cleared from the workflow state, so it can never re-fire.

## Notes

- An explicit `call_agent` beats workflow continuation — same priority tier as a human mention. Use it deliberately, not as a generic "next" mechanism.
- If you are inside a defined workflow and simply finishing the current step, use `dispatch` alone — let the workflow route the next step.
- Self-calls (`agent_name` = yourself) are blocked by the loop guard; do not emit them.
