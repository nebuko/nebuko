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

An agent ends its run with a single `AGENT_RUN_CONFIG` sentinel — a one-line
HTML comment carrying a compact JSON payload:

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->
```

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
> *every* sentinel, so the exact dispatch-only sentinel the `ctx` prompt is
> instructed to emit (`{"dispatch": {"status": "advance"}}`) could never pass
> validation — which is why `ctx` appeared not to call `genius` back. The
> `≥1 action` rule above replaces the old unconditional `task` requirement.

---

## 2. `dispatch` vs `function_call`

- `dispatch` = how **this run ends** inside a workflow. The engine decides the
  next step (see the cascade in §3). Use it when you are finishing a workflow
  step and want the workflow to route onward.
- `function_call` = an **imperative hand-off** to a named agent, overriding
  workflow continuation. Use it to summon a specific agent (e.g. `genius`
  pulling in `ctx`) rather than following the workflow edge.

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
5. **No target at all** → the **no-action default**: `hold`.

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
<!-- AGENT_RUN_CONFIG {"function_call": {"function": "call_agent", "agent_name": "ctx", "callback": "genius"}} -->
```

Behavior:

- `callback` **defaults to the emitting caller**. `genius` calling `ctx`
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

## 5. The no-action default → `hold`

An agent that ends a run with **no** `dispatch` and **no** `function_call` is
experiencing contract drift, not a clean completion. Auto-advancing the
workflow on an unknown state silently misroutes, so the safe response is to
**hold for a human** and record why:

```
status: hold
reason: "no-action default: agent emitted no decision"
```

If a sentinel marker was present but could not be parsed, the parse diagnostic
is carried into the reason instead of being swallowed:

```
reason: "no-action default: agent emitted no decision and the AGENT_RUN_CONFIG sentinel could not be parsed (<diagnostic>)"
```

The factory is `DispatchConfig.no_action_default(reason)`, applied in
`build_output._resolve_dispatch`.

> **Note for operators.** The hold surfaces in the workflow's normal
> awaiting-human path; there is no separate "ping the owner" channel in v1.
> A dedicated notification target is a documented extension point, not part of
> this contract.

---

## 6. Worked examples

### `genius` → `ctx` → `genius` (workflow edge)

`genius` finishes the `analyze` step with a plain dispatch advance; the
workflow edge `analyze → context` runs `ctx`. `ctx` answers and ends with:

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->
```

No `task`, and no callback is registered (this was a workflow-driven run), so
precedence falls to the workflow forward edge `context → plan`, which is
`genius`. This is the path that was broken by D1 and is now fixed.

### `issue` → `ctx` → `issue` (imperative call)

`issue` calls `ctx` imperatively:

```
<!-- AGENT_RUN_CONFIG {"function_call": {"function": "call_agent", "agent_name": "ctx"}} -->
```

The callback defaults to `issue`. `ctx` answers with a dispatch-only advance;
because a callback is registered it wins over the workflow edge and control
returns to `issue`.

### A decision-less agent

An agent posts an answer with no sentinel at all. The engine resolves
`dispatch = hold` with an observable reason (§5) rather than continuing the
workflow.
