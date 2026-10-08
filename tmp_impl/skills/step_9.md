# Step 9: Update docs and genius prompt for the gate-release contract

Issue #648. Documentation and the agent prompt must state the new, sole
release contract.

## What to do

### `docs/workflows.md` (human-gates section L91–113)

Rewrite the release contract:

- Only `/nebuko approve gate [--feedback "..."]` or
  `/nebuko reject gate [--feedback "..."]` from an authorized human
  (`OWNER`/`MEMBER`/`COLLABORATOR`, always-on) releases a gate.
- The agent is **never** re-invoked to confirm a gate.
- Agent classification of human intent is explicitly **NON-authoritative**.
- Reject **with** feedback re-runs the gated step (the gate re-arms on
  completion).
- Reject **without** feedback routes via `on_result: rejected`.
- `/nebuko resume` remains only for non-gated holds (e.g. soft-kill halts).

### `docs/architecture.md` (L205–215)

Replace the "the human resume is the authorization" text: the
`is_human_resume` bypass is removed and `GateDecision` is the sole release
artifact.

### `docs/reference.md` (L119 area)

Document the new `approve` / `reject` actions and the new `engine` command
class (handled inline by `resolve_config`; never dispatched to an agent).

### `defaults/prompts/genius.md` (L166–175)

Keep the gatekeeper classification UX, but state it **cannot** release a gate;
gate release happens in workflow space via the dedicated commands.

## Relevant file context

- `docs/workflows.md` L91–113; `docs/architecture.md` L205–215;
  `docs/reference.md` L119; `defaults/prompts/genius.md` L166–175.
- Current gate mechanics to correct: `apply_human_gate_intercept`
  (`build_output.py`), `_resolve_resume_path` (`resolve_route.py`),
  `BARE_RESUME_INPUT` / `HUMAN_RESUME_INPUT` (`parse_trigger.py`,
  `build_result.py`).

## Expected outcome

- Docs and prompt describe the dedicated gate commands, single release path,
  non-authoritative agent classification, and the reject feedback semantics.
- No stale references to "the human resume is the authorization" remain.

## Constraints

- Documentation/prompt-only changes; do not touch engine code in this step.
- Keep the diff focused on the gate contract.
