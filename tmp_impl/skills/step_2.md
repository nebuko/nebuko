# Step 2: Propagate pin from dispatch/output into workflow state

## Title

Step 2: Propagate `pin` from dispatch/output into workflow state

## Instructions

Make pinning reachable on the write path so Step 1's retention policy has real input.

**(a) Dispatch model.** Add `pin: bool = False` to the `Dispatch` model in `.github/scripts/workflow_state/sub_domains/dispatch/dispatch.py` and parse it from the `AGENT_RUN_CONFIG` sentinel / `dispatch_next` payload. Keep the existing `field_validator`/model conventions and document the new field in the class docstring.

**(b) build_output.** In `.github/scripts/agent_script/features/build_output/build_output.py`, read a new `PIN_COMMENT` env var (default `false`, parsed like the existing `INJECT_CONTEXT` at L939-940) and pass `pin=pin` into the `build_state(...)` call (build_state already accepts `pin`).

**(c) get_last_state.** In `.github/scripts/workflow_state/features/get_last_state/get_last_state.py`, extend `_enrich_history_with_comment_ids` (or add a sibling enrichment) so that the union of `comment_ids` from every history entry with `pin=True` is written into `state.pinned_comment_ids`, **idempotently** (no duplicates, stable order).

**(d) CI.** Add the `PIN_COMMENT` env passthrough to `.github/workflows/openhands-multi-agent.yml` where `INJECT_CONTEXT` is currently set.

**Tests.** Add/extend tests in `.github/scripts/workflow_state/features/build_state/test_build_state.py` and `.github/scripts/workflow_state/features/get_last_state/test_get_last_state.py` proving:

- dispatch `pin=true` → history entry `pin=True`;
- pinned history entry `comment_ids` → present in `state.pinned_comment_ids`;
- re-running enrichment does not duplicate ids.

## Expected outcome

- `Dispatch` carries a `pin` boolean parsed from the sentinel/dispatch payload.
- `build_output.py` reads `PIN_COMMENT` and forwards it into `build_state(..., pin=pin)`.
- `get_last_state` derives `state.pinned_comment_ids` from `pin=True` history entries idempotently.
- The workflow YAML threads `PIN_COMMENT` where `INJECT_CONTEXT` is set.
- New/updated tests are green; run the targeted suites and the broader `.github/scripts/` suite.

## Relevant file context

- `.github/scripts/workflow_state/sub_domains/dispatch/dispatch.py` — `Dispatch` model (fields + `validate_status`). No `pin` field today.
- `.github/scripts/agent_script/features/build_output/build_output.py` — `INJECT_CONTEXT` parsing (L939-940) and the `build_state(...)` call (L958). Also holds self-`function_call` neutralization logic (L897-903) — do not disturb.
- `.github/scripts/workflow_state/features/build_state/build_state.py` — `build_state(...)` already accepts `inject_context`/`pin` and writes them into the `HistoryEntry` (L20-73, L140-215).
- `.github/scripts/workflow_state/features/get_last_state/get_last_state.py` — `_enrich_history_with_comment_ids` (L49-93) currently enriches only `comment_ids`.
- `.github/scripts/workflow_state/workflow_state.py` — `WorkflowState.pinned_comment_ids`.
- `.github/workflows/openhands-multi-agent.yml` — locate where `INJECT_CONTEXT`/`PIN_COMMENT`-style env is set for the agent script run.
- Tests: `.github/scripts/workflow_state/features/build_state/test_build_state.py`, `.github/scripts/workflow_state/features/get_last_state/test_get_last_state.py`.

## Notes

- Keep the diff minimal and mirror existing parsing/validation patterns. Do not alter Step 1's read-side policy.
