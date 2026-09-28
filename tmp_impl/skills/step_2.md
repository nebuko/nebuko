# Step 2: LocalAdapter (file-backed run dir)

## Instructions

Create `adapters/local.py` implementing `PlatformAdapter` over a run dir
`runs/<run-id>/` containing: `trigger.json`, `journal.md`, `state.json`,
`latest_answer.md`, `console.log`.

- Add frozen `LocalRunPaths{root, trigger, journal, state, latest_answer, console}`
  and `LocalAdapter(run_id, root=Path('runs'))` with the five protocol methods.
- Journal writes **MUST** emit the standard `<!-- bot-workflow-terminal -->` and
  `<!-- bot-agent-* -->` markers so `loop_guard`, `circuit_breaker`, and state
  extraction parse them **unchanged**.
- `load_history` parses `journal.md` into a `CommentEntry` list.
- `save_state` round-trips `WorkflowState v1` JSON.
- Tests use **real filesystem temp dirs (no mocks)** and assert the markers are
  byte-compatible with the existing parsers.

No workflow-file changes.

## Expected outcome

- `adapters/local.py` provides `LocalRunPaths` and `LocalAdapter` satisfying the
  `PlatformAdapter` protocol (structurally conformant per `adapters/base.py`).
- `save_state`/`load_state` round-trip a `WorkflowState` losslessly.
- `load_history` returns `CommentEntry` objects parsed from `journal.md`.
- `post`/journal writes contain the exact marker strings consumed by
  `loop_guard.py`, `circuit_breaker.py`, and state extraction.
- Tests pass using `tmp_path` (no mocks, no network).
- No diff outside `adapters/` (+ new tests).

## Relevant file context

- Protocol to implement: `adapters/base.py` (from Step 1).
- Models: `engine/models.py` (`Trigger`, `CommentEntry`).
- Marker parsers to stay compatible with: `.github/scripts/loop_guard.py`,
  `.github/scripts/circuit_breaker.py`, and the state-comment read/write logic
  used by `resolve_config`.
- Existing script naming/marker conventions (e.g. `<!-- bot-workflow-terminal -->`,
  `<!-- bot-agent-<role> -->`, `<!-- WORKFLOW_STATE_START -->` /
  `<!-- WORKFLOW_STATE_END -->`) — mirror these exactly.
- New files: `adapters/local.py` + tests (e.g. `adapters/test_local.py`).
