# Step 5: Implement gate-decision routing path and resume guard

Issue #648 — the core routing change. This is the highest-complexity step.

## What to do

### 5a. New path in `resolve_route.py`

In `src/nebuko/engine/lib/resolve_config/features/resolve_route/resolve_route.py`
add:

```python
def _resolve_gate_decision_path(
    prev_state,
    wf_cmd,
    author,
    workflows_dir,
    local_workflows_dir=None,
) -> dict:  # @stub-protected
```

Validation chain — on any failure return `{"SKIP_AGENT": "true", "REASON": <explanation>}`:

1. `prev_state.dispatch is not None`, `prev_state.dispatch.status == "hold"`,
   and `(prev_state.dispatch.reason or "").startswith("human_gate:")`.
2. `author.may_decide_gate()` (Policy A allow-list `OWNER/MEMBER/COLLABORATOR`,
   **always-on for gate commands**).
3. Single-shot: no entry in `prev_state.gate_history` whose
   `is_for(prev_state.workflow, prev_state.step, prev_state.position)` matches
   the current hold (a second identical command is a no-op).

On success build a `GateDecision`:

```python
GateDecision(
    status="approved" if wf_cmd.action == "approve" else "rejected",
    workflow=prev_state.workflow,
    target_step=prev_state.step,
    decided_by=author.login,
    decided_at=<now, UTC>,
    feedback=wf_cmd.params.get("feedback", ""),
    comment_id=<deciding comment id when available>,
    position=prev_state.position,
)
```

Routing (locked decisions):
- **approved** → successor of the gated step via the gated step's
  `on_result: approved` condition.
- **rejected with non-empty feedback** → **re-run the gated step itself**
  (return a config targeting the same task/step) with the decision serialized
  in the routed config (`config_gate_decision`) for context injection. The gate
  re-arms when that re-run completes.
- **rejected with empty feedback** → successor via `on_result: rejected`.

### 5b. Persistence integration (IMPORTANT)

`_resolve_gate_decision_path` only returns an env-config dict. The decision
must be persisted into the new `WorkflowState` (`gate_decision` +
`gate_history`) by the run that consumes the routed config — mirror exactly how
`config_human_resume_input` flows today:

`resolve_route` returns `config_gate_decision` (serialized JSON)
→ `build_result.py` exports it as an env var (next to `HUMAN_RESUME_INPUT`, L186)
→ `build_output.py` reads it and passes it to `build_state(...)`
→ `build_state` appends it to `prev_state.gate_history` and sets `gate_decision`.

Add the `gate_decision` parameter + append/reset logic to
`src/nebuko/engine/lib/workflow_state/features/build_state/build_state.py`
(do not duplicate the model's construction there — receive the `GateDecision`).

### 5c. Wire into the cascade

In `src/nebuko/engine/lib/resolve_config/resolve_config.py`, in the routing
cascade next to the `resume` branch (L~183–214), add handling for
`wf_cmd.action in ("approve", "reject")` that calls `_resolve_gate_decision_path`.
Actions `approve`/`reject` are **never** dispatched to an agent. Build the
`AuthorIdentity` from the CI event env (see step 8) — never from body markers.

### 5d. Resume guard

In `_resolve_resume_path` (resolve_route.py L133): when
`prev_state.dispatch.status == "hold"` and
`prev_state.dispatch.reason.startswith("human_gate:")`, return
`SKIP_AGENT: "true"` with a reason pointing at `/nebuko approve gate` /
`/nebuko reject gate`. `/nebuko resume` remains valid only for non-gated holds
(e.g. soft-kill exhaustion).

## Relevant file context

- `resolve_route.py` helpers to reuse: `_workflow_successor` (L741),
  `_is_human_gate_step` (L758), `_resolve_handoff_decision` (L775),
  `_resolve_step_entry` (L1120), `_agent_step_entry` (L1065),
  `_apply_wf_marker` (L254), `_resolve_resume_path` (L133),
  `_resolve_dispatch_path` (L338), `_resolve_workflow_next_step` (L1522).
- Workflow condition matching lives in the `config` sub-domain
  `workflow_condition` (`StepRef`/`WorkflowStep.conditions`, `on_result`).
- `resolve_config.py` cascade: L~183 (resume), L~286 (held workflow stays
  held). Imports at L~20–40.
- Co-located tests: `.../features/resolve_route/test_resolve_route.py`
  (gate/resume tests around L328–547 — keep the `reason_code="gate_hold"`
  precedence tests passing) and `src/nebuko/engine/lib/test_resolve_config.py`.

## Tests

- Unauthorized identity → no-op (`SKIP_AGENT`), workflow stays held.
- Not held at a gate → no-op.
- Duplicate human approve (`gate_history` already has the matching decision)
  → second is a no-op (single-shot).
- Reject-with-feedback → re-runs the gated step, decision/feedback present in
  the routed config.
- Reject-without-feedback → follows `on_result: rejected`.
- Approve → follows `on_result: approved`.
- `/nebuko resume` on a `human_gate:*` hold → `SKIP_AGENT` with the pointer,
  stays held.

## Expected outcome

- `python -m pytest src/nebuko/engine/lib/resolve_config/ src/nebuko/engine/lib/test_resolve_config.py -q`
  is green.

## Constraints

- Do not modify any `# @stub-protected` signature.
- Keep existing resume/soft-kill semantics intact for non-gated holds.
