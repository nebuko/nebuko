# Step 2 / 5 — Deterministic, human-only gate release resolver in `resolve_config.py`

## Instructions

Goal: on `/workflow approve|reject gate`, resolve the held gated step's branch and emit a **skip-agent env contract** — no gated-agent turn runs and no LLM classification occurs.

1. In `.github/scripts/resolve_config/resolve_config.py`, add a helper `_gate_decision_env(wf_cmd, prev_state, issue_number, approver)` mirroring the existing `_delete_comments_env` (around L40). It must:
   - **a.** Require `prev_state` with `dispatch.status == "hold"` whose reason is `human_gate:<gate_step>` (or `prev_state.pending_gate_step`), and require the gated-step check (`_load_gated_step_def(workflow, gate_step)`, the same helper used in `build_output.py`) to confirm `human_gate: true`. Otherwise return `None`.
   - **b.** `decision = "approved" if wf_cmd.action == "approve" else "rejected"`.
   - **c.** Load the workflow definition and resolve the gated step's `conditions` for `on_result == decision`; capture `next_step`.
   - **d.** Build `GateDecision(decision=decision, gate_step=gate_step, approver=approver, decided_at=<utcnow ISO-8601>, reason=wf_cmd.params.get("reason"))` (import from `.sub_domains.gate_decision.gate_decision`).
   - **e.** Return the skip-agent env dict: `TASK`, `ROLE_NAME="script"`, `AGENT_SELECTED="false"`, `SKIP_AGENT="true"`, `GATE_DECISION=decision`, `GATE_STEP=gate_step`, `GATE_NEXT_STEP=next_step`, `GATE_DECISION_JSON=<gate.model_dump_json()>`.

2. Insert a new cascade branch **above** the `resume` branch (around L160): when `wf_cmd.action in {"approve", "reject"}` and `wf_cmd.resource == "gate"` and `not is_bot`, call the helper.
   - If it returns an env → return it.
   - If it returns `None` (no valid held gate) → return a deterministic error env (`SKIP_AGENT="true"`, `REASON="gate: no held human_gate step to <action>"`) so no agent runs and the human receives feedback.

3. Source `approver` from the acting comment author login available in `resolve_config`'s trigger context. If no such field is currently threaded, add it from the `issue_comment` payload. Bot-authored commands are already excluded by `is_bot`.

## Constraints

- The release path must be deterministic and outside agent space (no agent turn, no classification).
- Reuse the `_delete_comments_env` precedent and the existing `_load_gated_step_def` / workflow-definition loading helpers — do not invent a new transport or loader.
- Do not change the normal (non-gate) routing behaviour.

## Expected outcome

- Unit-testable via direct helper/function calls: a held gate + `approve gate` → env with `SKIP_AGENT=true`, `GATE_DECISION=approved`, and the correct `GATE_NEXT_STEP`; same for `reject gate`.
- No valid held gate → deterministic error env, never an agent dispatch.
- Existing `resolve_config` tests pass.

## Relevant files

- `.github/scripts/resolve_config/resolve_config.py` (`_delete_comments_env` precedent, cascade at ~L136–188, `resume` branch)
- `.github/scripts/resolve_config/features/resolve_route/resolve_route.py` (`_resolve_resume_path`, gate/hold handling)
- `.github/scripts/agent_script/features/build_output/build_output.py` (`_load_gated_step_def` reference implementation)
- `.github/scripts/resolve_config/sub_domains/gate_decision/gate_decision.py` (from step 1)
- `.github/scripts/resolve_config/sub_domains/workflow_command/workflow_command.py` (from step 1)

## Commit message

`[step 2/5] Deterministic, human-only gate release resolver in resolve_config.py`
