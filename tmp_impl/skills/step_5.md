# Step 5 / 5 — Remove agent-space gate authority, restrict `/workflow resume`, update docs

## Instructions

Goal: close the bypass surfaces and document the command.

1. `.agents/prompts/genius.md`:
   - **a.** In the human_gate handling, state that the branch now comes from the `/workflow approve|reject gate` command — the agent must **NOT** classify approval or emit `advance` to release a `human_gate`. On a gated step the agent's `advance` is unconditionally intercepted.
   - **b.** Remove/replace the approval-classification text that treats keyword judgment as the gate authority for `human_gate` steps (~L143–170); keep non-gated rhetorical classification only where it is not a security gate. Rewrite ~L161–170 to describe command-based release.

2. In `.github/scripts/resolve_config/features/resolve_route/resolve_route.py` (`_resolve_resume_path`, ~L128) or the `resume` branch in `resolve_config.py`: refuse to release a `human_gate` hold via `/workflow resume` — detect a `human_gate:` reason prefix and return a deterministic skip/error env instructing the human to use `/workflow approve gate` or `/workflow reject gate`. Non-gate holds (agent-emitted `hold`, Phase A proposals) must still resume normally.

3. Docs:
   - `docs/architecture.md` — the routing-cascade table gains `/workflow approve|reject gate`.
   - `docs/workflows.md` — the "Human Gates" mechanics are rewritten (release is a human-only command with skip-agent routing; no agent re-run/classification; remove or restate the loop re-trigger bypass text).
   - `docs/reference.md` — document `/workflow approve gate` and `/workflow reject gate` plus the `GateDecision` audit fields, and update the `steps[].human_gate` schema row to point at the command.

## Constraints

- No branch in `genius.md` may claim the agent can approve its own gate.
- `/workflow resume` must not release a gate; non-gate holds still resume.
- Minimal diff: docs and prompt text only, plus the resume guard.

## Expected outcome

- `genius.md` describes command-based release; no agent-side gate authority remains.
- `/workflow resume` on a `human_gate` hold is refused with guidance to use `approve|reject gate`.
- Docs describe the command and the `GateDecision` audit fields.

## Relevant files

- `.agents/prompts/genius.md` (human_gate boundary ~L143–170, approval classification ~L80–82)
- `.github/scripts/resolve_config/features/resolve_route/resolve_route.py` (`_resolve_resume_path` ~L128)
- `.github/scripts/resolve_config/resolve_config.py` (`resume` branch)
- `docs/architecture.md` (routing cascade, "The `human_gate` intercept")
- `docs/workflows.md` ("Human Gates" mechanics)
- `docs/reference.md` (`steps[].human_gate` schema row, `/workflow` command semantics)

## Commit message

`[step 5/5] Remove agent-space gate authority, restrict /workflow resume, update docs`
