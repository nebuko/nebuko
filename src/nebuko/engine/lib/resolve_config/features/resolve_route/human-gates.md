---
title: "Human gates"
audience: user
sidebar_group: "Guides"
sidebar_label: "Human gates"
sidebar_order: 13
---

# Human gates

`human_gate: true` is a security boundary enforced by the engine — not a
routing choice made by the agent. It guarantees that **no LLM can advance the
workflow past a gated step on its own**; only an identity-verified human
command releases it.

A gate release is an **engine-owned state mutation** (`GateDecision`), never an
agent action. The releasing command is recognized at comment-ingestion time and
keyed on the **GitHub event author** — never on comment-body markers, which are
agent-writable.

Mechanics:

1. The gated step's agent finishes and emits `advance` (or any other
   completing dispatch). The engine intercepts it and records `hold` (reason
   `human_gate:<step>`). The workflow stops; the step's output stays posted on
   the issue for human review. **Every** advancing dispatch is intercepted —
   the gate has no bypass. A held state also carries no forward edge: the
   intercept empties `resolution.next_step` and drops any agent-named
   `function_call`, and on any later bot trigger the resolver treats the
   `human_gate` hold as higher precedence than the workflow's next step or a
   `call_agent` hand-off, so neither a stale `plan -> implement` edge nor a
   persisted hand-off can advance the run (issues #885, #887).
2. An authorized human (GitHub `OWNER` / `MEMBER` / `COLLABORATOR`) reviews the
   output and runs one of:

   ```
   /nebuko approve gate [--feedback "..."]
   /nebuko reject gate  [--feedback "..."]
   ```

   The `--feedback` value may span the command line and every following line of
   the comment body (verbatim, newlines preserved); a trailing `--feedback`
   with no value is still rejected (issue #919).

   The command is handled **inline by the engine** and is never routed to an
   agent.
3. The engine records a `GateDecision` (`status`, `decided_by`, `decided_at`,
   `feedback`, and the exact hold instance it consumes) — the only artifact
   that can move the workflow past the gate — and routes from it:
   - **approved** → the gated step's `on_result: approved` successor;
   - **rejected with feedback** → the gated step is **re-run** with the
     feedback injected into its context; the gate re-arms when that run
     completes, so fresh human validation is required;
   - **rejected without feedback** → the `on_result: rejected` successor.

## Gate verdict routing (issue #1274)

**How to use.** A gated step may declare conditional edges alongside
`human_gate: true`, exactly like any other step:

```yaml
  - name: plan
    agent: architect
    human_gate: true
    next: implement
    conditions:
      - on_result: approved
        next: implement
      - on_result: rejected
        next: context
```

(This is the shape shipped by [`defaults/workflows/default.yaml`](../../../../../../../defaults/workflows/default.yaml): a rejected plan
sends the run back to `context` for fresh facts.)

**How it works — verdict routing.** When a gated step's agent ends its turn
with a verdict, the gate guard routes on it *before* parking:

| Agent verdict | Gate has `on_result: rejected` edge? | What happens |
|---|---|---|
| `rejected` | yes | The conditional reject edge is routed **automatically** (including backward edges). No human ping, no hold. The armed `GateLock` is consumed on that route so the next turn is not re-parked; the gate re-arms naturally when the gated step completes again. |
| `rejected` | no | Falls back to the human hold — it never silently follows the forward `next` edge. |
| `approved` / default / absent | — | Unchanged: parked in `human_gate_hold` until `/nebuko approve gate` / `/nebuko reject gate`. The gate pings the human **only** before moving forward. |

Notes:

- The consult budget (`call_agent` rounds while armed) does not apply to the
  rejected route; it stays bound to the hold path.
- The reject route is still bounded: after `MAX_CONSECUTIVE_AUTO_REJECTS = 3`
  consecutive auto-routed rejects on the same gated step (constant in
  `src/nebuko/lib/resolve_config/features/resolve_route/resolve_route.py`),
  the run falls back to the human hold with the reason in the hold note. The
  counter resets on any human gate decision or on an `approved` verdict for
  the gated step.
- Human gate **command** semantics are untouched: a rejected-with-feedback
  command still re-runs the gated step with the feedback injected.

The example above mirrors the shape shipped in
[`defaults/workflows/default.yaml`](../../../../../../../defaults/workflows/default.yaml)
(the `plan` step).

4. Decisions are **single-shot**: a `GateDecision` binds to exactly one
   `(workflow, step, position)` hold instance and is consumed on routing, so
   replaying an old command is a no-op.

**Agents complete a gated step with `advance`, never `hold`.** The gate is
released only by the human command above, so the agent's job is to deliver its
output and advance; the engine converts that completing dispatch into the hold.
An explicit `hold` would stop *before* the gate and require a second, non-gate
resume — the double-approval anti-pattern.

**Non-authoritative agent classification.** Agents may still read and classify
human intent for UX (a role prompt may note "the human approved"), but that
classification is **not** what releases a gate — only the engine-recorded
`GateDecision` does.

**Identity is authoritative.** A gate command is accepted only when its GitHub
event author is a non-bot user whose `author_association` is
`OWNER` / `MEMBER` / `COLLABORATOR`, and the comment body carries no machine
token (`<!-- bot-... -->`, `AGENT_RUN_CONFIG`, …). A comment authored by the CI
bot that merely *contains* `/nebuko approve gate` is inert, and so is a
privileged human echoing agent output that carries a machine token.

**`/nebuko resume` is unchanged for non-gated holds.** It still releases a
`soft_kill` exhaustion halt (issue #704), but on a `human_gate:*` hold it is a
no-op that points at the gate commands — one release path per hold class.

**Engine-owned human notification (issue #795).** Agents never ping a human.
When a run stops for human attention the engine itself posts one
`HumanActionNotice` comment naming the workflow starter
(`@started_by`), so the person who launched the workflow is asked for
validation. The notice is classified from the run outcome:

| Stop | Notice |
|---|---|
| `hold`, reason `human_gate:<step>` | *awaiting approval* |
| `hold`, any other reason (incl. a rudderless no-action default, §5 of [handoff-and-callback.md](handoff-and-callback.md)) | *awaiting input* |
| terminal step, on the run that produced its output | *workflow complete* |
| `soft_kill` while the soft-retry budget remains | none (transient retry) |
| terminal `SKIP_AGENT` on a follow-up run | none (nothing new to act on) |
| gate command with nothing it may decide (issue #1012) | none — the engine posts the skip reason (`skipping agent — <reason>`) and exits 0 |

The recipient is the **workflow starter** (`WorkflowState.started_by`, captured
from the first trigger and carried forward), falling back to the issue author
and then the trigger author. Agent-authored comment bodies are made
notification-inert by the publish path, so an agent mentioning `@someone` is
readable but does not notify; fenced blocks and inline code are left verbatim
(GitHub never notifies for a mention there, so rewriting one would only corrupt
the snippet).

The gate intercepts only `advance` (and an absent dispatch). `terminate` is
never intercepted, and `loop` retries within a step are not intercepted.
