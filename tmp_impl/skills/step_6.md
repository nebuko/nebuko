# Step 6: Harden human_gate intercept — remove is_human_resume bypass

Issue #648. There must be **no second release path**: a gated step can only be
left via a recorded `GateDecision` (step 5).

## What to do

In `src/nebuko/engine/lib/agent_script/features/build_output/build_output.py`:

1. Change the signature of `apply_human_gate_intercept` (L333) to:
   ```python
   def apply_human_gate_intercept(dispatch, workflow_name, workflow_step):
   ```
   **Drop** the `is_human_resume` parameter and the `if is_human_resume: return dispatch`
   bypass (L360) entirely. Update the docstring accordingly.
   On a gated step **every** completing dispatch — `advance`, `soft_kill`,
   `None` (→ advance), and branch-bearing dispatches — is rewritten to
   `hold` with `reason=f"human_gate:{workflow_step}"`. An agent-supplied
   `branch` on a gated step is inert: clear it in the `model_copy` update
   (e.g. `update={"status": "hold", "reason": ..., "branch": None}`).
2. At the call site (L~1007–1022) remove the `HUMAN_RESUME_INPUT` check from
   the intercept call. **Keep** the `is_human_resume` variable: it is still
   passed to `build_state(...)` (L~1050–1063) where it resets the soft-kill
   retry budget (`build_state.py` L242). Only the gate-authorization role is
   removed.
3. In `src/nebuko/engine/lib/resolve_config/features/parse_trigger/parse_trigger.py`
   update the `BARE_RESUME_INPUT` docstring (L~205–210): it is kept as the
   sentinel for bare-resume feedback text only; it no longer carries any
   gate-authorization meaning.

## Relevant file context

- `build_output.py`: `_load_gated_step_def` (L305), `apply_human_gate_intercept`
  (L333–372), call site (L~1000–1065). `Dispatch` model:
  `src/nebuko/engine/lib/workflow_state/sub_domains/dispatch/dispatch.py`
  (fields include `branch`, `reason`).
- `build_state` uses `is_human_resume` at
  `src/nebuko/engine/lib/workflow_state/features/build_state/build_state.py` L242.

## Tests to update

`src/nebuko/engine/lib/agent_script/features/build_output/test_build_output.py`
`TestHumanGateIntercept` (L321–511) and
`src/nebuko/engine/lib/agent_script/features/build_output/test_build_output_soft_kill.py` (L137–148):

- The old "resume passes the gate" tests now assert **hold**:
  `test_gate_step_advance_resume_bypasses` (L351),
  `test_gate_step_soft_kill_resume_bypasses` (L397),
  `test_real_yaml_gated_step_resume_bypasses` (L499),
  `test_gate_step_keeps_soft_kill_on_human_resume` (soft_kill file L144).
- Retain `soft_kill -> hold`.
- Add: a branch-bearing dispatch on a gated step → `hold` with an inert
  (cleared) branch.

## Expected outcome

- `python -m pytest src/nebuko/engine/lib/agent_script/features/build_output/ -q`
  is green.
- `apply_human_gate_intercept` has no `is_human_resume` parameter.

## Constraints

- Do not remove `is_human_resume` from `build_state` (unrelated soft-retry
  reset behaviour).
- Do not modify `# @stub-protected` signatures.
