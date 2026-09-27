# Step 3 / 5 — Persist pending gate step and suppress re-hold in `build_output.py`

## Instructions

Goal: make the hold durable/recorded and prevent a stale loop from re-blocking or bypassing a gate once a decision exists.

1. In `.github/scripts/agent_script/features/build_output/build_output.py`, extend `apply_human_gate_intercept` (~L217) and its call site (~L800): when intercepting an `advance` on a gated step, keep `dispatch.reason = "human_gate:<workflow_step>"` and carry the gated step name forward so the written state records `pending_gate_step=<workflow_step>`.

2. Suppress re-hold when the completing run carries a recorded `GateDecision` for that step: if the current `WorkflowState.gate` is present and `gate.gate_step == workflow_step`, do **not** intercept (a human already decided). This is defensive — with the deterministic release path no agent runs — but it prevents any residual loop from re-blocking.

3. Ensure the state written at the end of a run persists `pending_gate_step` while held, and that the existing state-write path clears `gate`/`pending_gate_step` once routed. **Reuse the existing state-write path in `build_output`** — do not invent a new one.

## Constraints

- Minimal diff: extend `apply_human_gate_intercept` and its call site only.
- Do not change the signature in a breaking way; keep existing callers working.
- Reuse the existing state-write path; no new transport.

## Expected outcome

- A held run persists `pending_gate_step` in `WorkflowState`.
- A run carrying a matching `GateDecision` for the step is not re-held.
- Existing gate-intercept unit tests still pass (`python -m pytest .github/scripts/agent_script/features/build_output/` — run the relevant test module(s)).

## Relevant files

- `.github/scripts/agent_script/features/build_output/build_output.py` (`_load_gated_step_def` ~L184, `apply_human_gate_intercept` ~L217, call site ~L800)
- `.github/scripts/workflow_state/workflow_state.py` (`WorkflowState.gate`, `WorkflowState.pending_gate_step` — added in step 1)
- Existing build_output test module(s) for the gate intercept.

## Commit message

`[step 3/5] Persist pending gate step and suppress re-hold in build_output.py`
