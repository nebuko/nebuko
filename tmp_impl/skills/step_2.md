# Step 2: Extend WorkflowState with gate_decision and gate_history

Issue #648. The workflow state (the `<details>` JSON block) must carry the
engine-owned gate decision so it survives across runs.

## What to do

In `src/nebuko/engine/lib/workflow_state/workflow_state.py`:

1. Import the new model next to the other sub-domain imports (around lines
   5–11, e.g. `from .sub_domains.dispatch.dispatch import Dispatch`):
   ```python
   from .sub_domains.gate_decision.gate_decision import GateDecision
   ```
2. Add two fields next to the `dispatch` field (around line 138):
   ```python
   gate_decision: Optional[GateDecision] = None            # latest decision
   gate_history: list[GateDecision] = Field(default_factory=list)  # full append-only audit
   ```
   (The human explicitly chose the **full** `gate_history` audit — Q3.)
3. Add matching entries to the class docstring's `Fields:` block (mirror the
   existing per-field documentation style).

## Relevant file context

- `src/nebuko/engine/lib/workflow_state/workflow_state.py` — `WorkflowState`
  fields live at lines ~123–154; `dispatch` at line 138; `position` uses the
  same `list[StepRef] = Field(default_factory=list)` shape.
- State serialization / codec: `src/nebuko/engine/lib/workflow_state/` also
  contains `test_state_codec.py` and `test_workflow_state.py` — extend these
  for the round-trip coverage below.
- State markers: `STATE_MARKER_START`/`STATE_MARKER_END` at the top of the
  same file.

## Tests

- A `WorkflowState` with and without gate decisions serializes to the
  workflow-state `<details>` JSON and back **losslessly**.
- `gate_history` order is preserved (append-only).
- Older state blobs without the two keys load with the defaults
  (`None` / `[]`) — no validation error.

## Expected outcome

- `python -m pytest src/nebuko/engine/lib/workflow_state/ -q` is green.
- A round-trip through the state codec preserves `gate_decision` and
  `gate_history`.

## Constraints

- Do not modify `SoftRetryState` or any existing `# @stub-protected`
  signature.
- Backward compatibility: legacy state blobs must keep loading unchanged.
