# Stage A / Step 1: Shared models + PlatformAdapter protocol

## Instructions

Create two new top-level packages.

1. **`engine/__init__.py` + `engine/models.py`**: Pydantic models
   - `Trigger{author: str, body: str, type: str, number: int}`
   - `CommentEntry{author: str, body: str, created_at: str}` where `created_at` is an ISO-8601 string
   - Re-export the existing `WorkflowState` by importing it from `.github/scripts/workflow_state/workflow_state.py` (do NOT copy or modify that module; `VERSION` stays 1).
   - Add a small, explicit path helper (e.g. a function returning the `.github/scripts` directory resolved from the repo root) so engine modules can import the existing scripts deterministically in tests.

2. **`adapters/__init__.py` + `adapters/base.py`**: `class PlatformAdapter(Protocol)` with exactly five methods:
   - `read_trigger(self) -> Trigger`
   - `load_history(self) -> list[CommentEntry]`
   - `post(self, body: str) -> None`
   - `load_state(self) -> Optional[WorkflowState]`
   - `save_state(self, state: WorkflowState) -> None`

3. **Tests**:
   - `engine/test_models.py` — field defaults, JSON serialization round-trip of both models.
   - `adapters/test_base.py` — a trivial in-memory fake adapter satisfying the protocol structurally; assert `load_state`/`save_state` round-trip by posting `to_git_comment(state)` into a journal and recovering it with `extract_state` from the same modules.

## Constraints

- No existing file may be modified.
- No file under `.github/workflows/`, `workflows/`, or `agent-config.yaml` may be touched.
- Do not change the `WORKFLOW_STATE v1` schema.

## Expected outcome

- `engine/` and `adapters/` packages exist with the models and protocol above.
- All new tests pass (`python -m pytest engine/test_models.py adapters/test_base.py`).
- `git status` shows only new files under `engine/`, `adapters/`.

## Relevant files

- `.github/scripts/workflow_state/workflow_state.py` (read-only reference: `WorkflowState`, `to_git_comment`, `extract_latest_state_from_comments`)
- `.github/scripts/workflow_state/features/get_last_state/get_last_state.py` (`extract_state`)
- `.github/scripts/workflow_state/sub_domains/dispatch/dispatch.py` (`Dispatch`, `DISPATCH_STATUSES`)

## Commit message

`[step 1/6] Stage A / Step 1: Shared models + PlatformAdapter protocol`