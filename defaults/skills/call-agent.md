---
description: "Hand off to a specific next agent with `turn_outcome.agent_call`, overriding the default workflow continuation."
---

# Calling Another Agent

Use this to hand off to a specific agent of your choice. This is an **imperative** signal: you decide who runs next, overriding workflow continuation.

## When to use which signal — one-line rule

- `finalize(turn_outcome={appreciation:…})` = how **this run ends** inside a workflow (the engine decides the next step).
- `finalize(turn_outcome={agent_call:…})` = **imperative hand-off** to a specific agent, outside or over the workflow.

Never use `@@agent` mentions to call another agent: bot messages are ignored for `@@` tokens (issue #478), and stray `@@` in prose can mis-trigger CI runs.

## Format

Use the typed `finalize` control-plane tool — it records the hand-off structurally, immune to long-context format drops.

Hand-off fields (do not write a sentinel yourself — the tool records the hand-off structurally):

- **One `agent_call` per turn.** Supply exactly one; a later `finalize` call in the same turn replaces it.
- `agent_call.agent_name` must be a known agent roster name: `architect`, `context`, `dev`, `review`, `po`.
- `agent_call.task` (optional): the callee's only user prompt. It is the sole task description the callee sees — it does not inherit your conversation.

## Return path and precedence (issues #765, #1033)

The callee is run with only its `agent_call.task` plus the standard system prompt; the callback (your return point) is engine-owned run state and is never sent to it.

When a callee finishes, the engine resolves its successor in this order (highest → lowest):

1. The callee's own explicit `agent_call` → route there.
2. The callee's own `hold` verdict (`finalize(turn_outcome={appreciation:'hold', hold_kind:'awaiting_input'})`) → recall the callee with the human's answer; a deliberate stop is never redirected to you.
3. The callee's `approved` or `rejected` verdict → the engine invokes the callback: you get a new turn to finalize that receives the callee's full report, and you choose your own verdict.
4. The workflow forward edge (`steps[].next`) → continue the workflow.
5. No target at all → the run records `INCOMPLETE`/`NO_OUTCOME` (the no-action safety net; never auto-advance an unknown state).

`finalize(turn_outcome={appreciation:'approved'})` means "I am done — route me by the standard rules above". The callback (step 3) wins over the workflow edge (step 4) because it is explicit caller intent, but the callee's own action (steps 1–2) always wins over both.

## Notes

- An explicit `agent_call` beats workflow continuation — same priority tier as a human mention. Use it deliberately, not as a generic "next" mechanism.
- If you are inside a defined workflow and simply finishing the current step, use `appreciation` alone — let the workflow route the next step.
- Self-calls (`agent_name` = yourself) are blocked by the loop guard; do not emit them.
