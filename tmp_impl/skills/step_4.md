# Step 4 / 5 — Harness skip-agent gate routing + audit state write

## Instructions

Goal: route a gate decision to the next step with **no agent turn**, and record the audit decision.

1. In `.github/workflows/openhands-multi-agent.yml`, extend the existing script/delete skip contract (~L328–372, especially ~L344–358). When `GATE_DECISION` is non-empty: append `SKIP_AGENT=true`, `GATE_DECISION`, `GATE_STEP`, `GATE_NEXT_STEP`, `GATE_DECISION_JSON`, and `ROLE_NAME=script` to `$GITHUB_ENV`, then `exit 0` — no agent turn.

2. Add/extend a deterministic script (patterned on `delete_comments.py`, the existing delete path) invoked on this skip branch that:
   - **a.** Writes/updates the workflow state so the next step is `GATE_NEXT_STEP`, and embeds the `gate` audit record (decoded from `GATE_DECISION_JSON`) in `WorkflowState`.
   - **b.** Posts a short human-facing confirmation comment: `Gate <step> <decision> by <approver>; advancing to <next_step>`.
   - Reuse the repo's existing state-posting mechanism and comment-posting helper — do not invent new transport.

3. Confirm the continue-token gate (~L884–907, `dispatch_status in ("advance", "loop")`) is unaffected for normal runs.

## Constraints

- No agent turn on the gate-decision path (skip-agent contract).
- Reuse existing state-posting and comment-posting helpers (mirror the `delete_comments.py` invocation pattern).
- Do not alter normal-run continue-token behaviour.

## Expected outcome

- A `GATE_DECISION` env produces a no-agent run that advances to `GATE_NEXT_STEP` with the audit decision persisted in `WorkflowState`.
- A human-facing confirmation comment is posted.
- Normal runs are unchanged.

## Relevant files

- `.github/workflows/openhands-multi-agent.yml` (script/delete skip contract ~L328–372; continue-token ~L884–907)
- The existing deterministic `delete_comments.py` script (invocation + state/comment post pattern to mirror)
- `.github/scripts/resolve_config/resolve_config.py` (env contract emitted in step 2: `GATE_DECISION`, `GATE_STEP`, `GATE_NEXT_STEP`, `GATE_DECISION_JSON`)
- `.github/scripts/workflow_state/workflow_state.py` (`WorkflowState.gate`, `pending_gate_step`)

## Commit message

`[step 4/5] Harness skip-agent gate routing + audit state write`
