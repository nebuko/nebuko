# Step 1: Add GateDecision model (engine-owned gate record)

Issue #648 — make the `human_gate` release an engine-owned state mutation
released only by an identity-verified human command.

## Goal

Create the immutable, attributed record of a human's gate decision — the
**only** artifact that can move a workflow past a `human_gate` step.

## What to do

Create a new sub-domain package mirroring the existing
`src/nebuko/engine/lib/workflow_state/sub_domains/dispatch/` layout:

- `src/nebuko/engine/lib/workflow_state/sub_domains/gate_decision/__init__.py`
  — export `GateDecision`.
- `src/nebuko/engine/lib/workflow_state/sub_domains/gate_decision/gate_decision.py`
  — define:

```python
class GateDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["approved", "rejected"]   # @stub-protected
    workflow: str                             # @stub-protected
    target_step: str                          # @stub-protected
    decided_by: str                           # GitHub login of the deciding human
    decided_at: datetime                      # @stub-protected
    feedback: str = ""
    comment_id: Optional[int] = None          # audit link to the deciding comment
    position: list[StepRef] = Field(default_factory=list)  # consumed hold instance

    def is_for(self, workflow: str, step: str, position: list[StepRef]) -> bool:  # @stub-protected
        """True iff the decision targets that exact (workflow, step, position) triple."""
```

Responsibility (one line): *immutable, attributed record that a human decided
a specific gate.*

## Relevant file context

- Reference model shape: `src/nebuko/engine/lib/workflow_state/sub_domains/dispatch/dispatch.py`
  (pydantic `BaseModel`) and its `__init__.py`.
- Reference sub-domain with a co-located test:
  `src/nebuko/engine/lib/workflow_state/sub_domains/function_call/`
  (`function_call.py` + `test_function_call.py`).
- `StepRef` is defined in
  `src/nebuko/engine/lib/config/sub_domains/step_ref/step_ref.py` and is
  imported as `from config import StepRef` (see
  `workflow_state/workflow_state.py` line 4). It is the same type as
  `WorkflowState.position`.
- Import root: `pyproject.toml` sets `pythonpath = ["src", "src/nebuko/engine/lib"]`,
  so `from config import StepRef` works from anywhere under `engine/lib`.

## Tests (co-located `test_gate_decision.py`)

- Field construction + defaults (`feedback == ""`, `comment_id is None`,
  `position == []`).
- `model_config` enforcement: assigning a field / passing an extra key raises.
- `is_for` matches on identical (workflow, step, position); mismatches on any
  of the three are `False`.
- JSON round-trip: `GateDecision.model_validate_json(d.model_dump_json()) == d`.

## Expected outcome

- `python -m pytest src/nebuko/engine/lib/workflow_state/sub_domains/gate_decision/test_gate_decision.py -q`
  is green.
- Importing `from workflow_state.sub_domains.gate_decision import GateDecision`
  succeeds.

## Constraints

- Do not modify any `# @stub-protected` signature.
- Keep the change inside the new `gate_decision/` package only.
