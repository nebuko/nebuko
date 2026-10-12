# Hand-off and Callback

How agents hand work to each other and how control returns. This document is
the normative contract for the `AGENT_RUN_CONFIG` sentinel, the `dispatch` vs
`function_call` distinction, and the **callback** return address introduced in
issue #765.

For the raw schema tables see [reference.md](./reference.md). For the routing
cascade that consumes these decisions see
[architecture.md](./architecture.md).

---

## 1. Two signals, one sentinel

A run's hand-off is recorded as a single `AGENT_RUN_CONFIG` sentinel — a one-line
HTML comment carrying a compact JSON payload:

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->
```

The sentinel is **engine-rendered** from typed state (the `dispatch_next` /
`call_agent` control-plane tools) and appended to the posted comment exactly
once; agents no longer emit it themselves (issue #876). The parser described
here reads it back as a durable cross-run record and as a compatibility input
for legacy comments.

The payload may carry up to three top-level actions:

| Field | Meaning | Decides the next agent? |
|-------|---------|-------------------------|
| `task` | Legacy workflow-relative selection (name the target role). | yes |
| `dispatch` | How **this run ends** inside a workflow. | only when it is a `hold`/`loop`/`terminate`, or when it names an explicit `task_hint`/`workflow`/`step` |
| `function_call` | **Imperative hand-off** to a specific agent, over the workflow. | yes |

### The contract invariants

The canonical model is `AgentRunConfig`
(`engine/lib/config/sub_domains/agent_run_config/agent_run_config.py`).
Two invariants are enforced at validation time:

1. **At least one action is required.** A sentinel must carry one of `task`,
   `dispatch` or `function_call`. An empty payload is invalid — this is the
   *no-action* case that historically ended a run with nothing actionable.
2. **`task` and `function_call` are mutually exclusive.** They are two
   different ways to name a target; combining them is ambiguous and rejected
   (the legacy ambiguity guard).

> **Defect fixed (D1).** Before issue #765 the validator required `task` on
> *every* sentinel, so the exact dispatch-only sentinel the `context` prompt is
> instructed to emit (`{"dispatch": {"status": "advance"}}`) could never pass
> validation — which is why `context` appeared not to call `architect` back. The
> `≥1 action` rule above replaces the old unconditional `task` requirement.

---

## 2. `dispatch` vs `function_call`

- `dispatch` = how **this run ends** inside a workflow. The engine decides the
  next step (see the cascade in §3). Use it when you are finishing a workflow
  step and want the workflow to route onward.
- `function_call` = an **imperative hand-off** to a named agent, overriding
  workflow continuation. Use it to summon a specific agent (e.g. `architect`
  pulling in `context`) rather than following the workflow edge.

They are documented as a skill in
[`skills/call-agent.md`](../defaults/skills/call-agent.md).

---

## 3. Routing precedence

When an agent finishes, the engine resolves its successor in this order
(highest priority first):

1. **The callee's own explicit `function_call`** → route to `agent_name`.
2. **The callee's own explicit `dispatch` `hold` / `loop` / `terminate`** →
   honor it (a deliberate stop, retry, or human gate).
3. **The registered `callback`** → return to the caller.
4. **The workflow forward edge** (`steps[].next`) → continue the workflow.
5. **No target at all** → the **no-action default**: return to the `callback`
   when a return address is registered, else `hold` (§5).

`dispatch` with `status: advance` and no explicit destination means *"I am
done — route me by the standard rules above"*. Rule 2 only fires for the
non-advance statuses; an `advance` falls through to the callback (3), then the
workflow edge (4).

---

## 4. The callback return address

A `function_call` may carry a `callback`: the agent to run when the callee
finishes **without a decision of its own**. This is the *agent-as-tools*
pattern — the caller delegates a sub-task and control returns to it — as
opposed to a pure hand-off where control moves on.

```
<!-- AGENT_RUN_CONFIG {"function_call": {"function": "call_agent", "agent_name": "context", "callback": "architect"}} -->
```

Behavior:

- `callback` **defaults to the emitting caller**. `architect` calling `context`
  therefore does not have to write the field — the engine fills it in.
- It is a **fallback**, not an override: the callee's own action (precedence
  rules 1–2) always wins.
- It is **advance-only**: a callee that explicitly `hold`s or `loop`s is never
  redirected to the callback.
- A `callback` equal to the callee's own `agent_name` is a no-op (no self
  re-entry).
- It is **cleared on every non-function-call transition**, so a stale callback
  cannot re-fire on a later step.

Where the callback lives: `WorkflowState.callback` (persisted in the posted
workflow state as `CONFIG_CALLBACK`), resolved by
`resolve_route._callback_return_target`.

---

## 5. The no-action default → callback, else `hold`

An agent that ends a run with **no** `dispatch` and **no** `function_call` is
experiencing contract drift, not a clean completion. What happens next depends
on whether the run carries a **return address** (`callback`):

- **No return address** (a rudderless top-level run): hold for a human. Auto-
  advancing the workflow on an unknown state silently misroutes, so the safe
  response is to stop and record why:

  ```
  status: hold
  reason: "no-action default: agent emitted no decision"
  ```

- **With a return address** (the run was reached through a caller's
  `function_call`): return to the caller. A callee that produced a deliverable
  and has a known caller is a *bounded continuation* — the caller re-enters
  with the report and decides — not a dead-end hold (issue #864):

  ```
  status: advance
  reason: "no-action default: agent emitted no decision; returning to caller '<callback>'"
  ```

If a sentinel marker was present but could not be parsed, the parse diagnostic
is carried into the reason instead of being swallowed:

```
reason: "no-action default: agent emitted no decision and the AGENT_RUN_CONFIG sentinel could not be parsed (<diagnostic>)"
```

**Provenance.** Every synthesized fallback is tagged
`origin: "engine_fallback"` on the `Dispatch` (an agent-emitted decision
defaults to `origin: "agent"`), so consumers can tell a fabricated routing
decision apart from one the agent actually made. The precedence rule 2 (§3)
is unchanged: an **explicit** agent `hold`/`loop`/`terminate` always outranks
the callback — only the synthesized fallback returns home.

The factory is `DispatchConfig.fallback_for(callback, diagnostic)`, applied in
`build_output._resolve_dispatch`. `DispatchConfig.no_action_default(reason)`
remains as the rudderless compatibility shim (always `hold` +
`engine_fallback`).

> **Engine-owned human notification (issue #795).** The hold surfaces in the
> workflow's normal awaiting-human path, and the engine — not the agent — posts
> the notification. On every stop-for-a-human outcome (`human_gate` hold, plain
> hold, terminal step) the engine emits one `HumanActionNotice` mentioning the
> workflow starter (`WorkflowState.started_by`, falling back to the issue author
> and then the trigger author). Agents never ping a human: their comment bodies
> are made notification-inert by the publish path. A dedicated per-step
> notification target remains a documented extension point.

---

## 6. Worked examples

### `architect` → `context` → `architect` (workflow edge)

`architect` finishes the `analyze` step with a plain dispatch advance; the
workflow edge `analyze → context` runs `context`. `context` answers and ends with:

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->
```

No `task`, and no callback is registered (this was a workflow-driven run), so
precedence falls to the workflow forward edge `context → plan`, which is
`architect`. This is the path that was broken by D1 and is now fixed.

### `issue` → `context` → `issue` (imperative call)

`issue` calls `context` imperatively:

```
<!-- AGENT_RUN_CONFIG {"function_call": {"function": "call_agent", "agent_name": "context"}} -->
```

The callback defaults to `issue`. `context` answers with a dispatch-only advance;
because a callback is registered it wins over the workflow edge and control
returns to `issue`.

### A decision-less agent

An agent posts an answer with no sentinel at all. With **no** return address
the engine resolves `dispatch = hold` (`origin="engine_fallback"`) with an
observable reason (§5) rather than continuing the workflow. If the run was a
callee reached through a caller's `function_call`, the same fallback resolves
`advance` and returns to the caller instead.

---

## 7. What the agent is told (injected team context)

The delegation and call-back contract above is **engine-enforced**; the prompt
only carries a reminder, injected once per run by
`resolve_config/features/team_context/team_context.py`. Every built system
prompt gains one **"Your Team"** section listing the agent's own role, its
teammates' roles (from each agent's config `role:`), and two rules:

- work outside your own tool scope must be delegated through the `call_agent`
  mechanism (see [`call-agent.md`](../defaults/skills/call-agent.md)) — `@@`
  mentions are for humans only (issue #478);
- when a teammate calls you, your answer is returned to them by the engine's
  callback (precedence rule 3) — treat it as a conversation turn, not
  fire-and-forget.

When the run was started by a bot call, the section also names the caller:
`You were called by <caller> — your result returns to <caller>.` The reminder is
worded to match this contract literally; it adds no mechanism of its own. See
[agents-and-skills.md — Team Context Injection](./agents-and-skills.md#team-context-injection).
