# Step 1: engine models + PlatformAdapter protocol

## Instructions

Create `engine/models.py` with frozen dataclasses
`Trigger{author:str, body:str, type:str, number:int}` and
`CommentEntry{author:str, body:str, created_at:datetime}`.
Reuse the existing Pydantic `WorkflowState` from
`.github/scripts/workflow_state/workflow_state.py` (**do NOT redefine**).

Create `adapters/base.py` defining the `PlatformAdapter` `Protocol` with method
signatures:

```python
read_trigger() -> Trigger
load_history(trigger: Trigger) -> Sequence[CommentEntry]
post(trigger: Trigger, body: str) -> None
load_state(trigger: Trigger) -> WorkflowState | None
save_state(trigger: Trigger, state: WorkflowState) -> None
```

No method bodies beyond `Protocol` ellipses. Add `engine/__init__.py` and unit
tests asserting dataclass immutability and protocol structural conformance.

Do **not** touch any GitHub Actions workflow file.

## Expected outcome

- `engine/models.py` exists with immutable `Trigger` and `CommentEntry`
  (frozen dataclasses; mutation raises).
- `adapters/base.py` declares `PlatformAdapter` as a `typing.Protocol`; a
  conforming dummy class is accepted structurally and a non-conforming one fails
  `isinstance` checks where applicable.
- `engine/__init__.py` exists.
- New tests pass (e.g. `pytest engine/ adapters/`).
- No diff outside `engine/` and `adapters/` (plus the new tests).

## Relevant file context

- Existing state model to reuse: `.github/scripts/workflow_state/workflow_state.py`
  (`WORKFLOW_STATE v1`, Pydantic `BaseModel`) — import it, do not copy it.
- New files: `engine/models.py`, `engine/__init__.py`, `adapters/base.py`,
  plus tests (e.g. `engine/test_models.py`, `adapters/test_base.py`).
- Reference for the GHA orchestration semantics (read-only):
  `.github/workflows/openhands-multi-agent.yml`.
- Python convention skill: `skills/python.md`.
