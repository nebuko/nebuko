# Step 1 / 5 — `/workflow approve|reject gate` grammar + `GateDecision` model + `WorkflowState` fields

## Instructions

Goal: extend the command grammar and add the durable gate-decision domain object. **Do NOT touch routing yet** (that is step 2).

1. In `.github/scripts/resolve_config/sub_domains/workflow_command/workflow_command.py`:
   - Add `"approve"` and `"reject"` to the `ACTIONS` tuple.
   - Add `RESOURCE_BY_ACTION["approve"] = "gate"` and `RESOURCE_BY_ACTION["reject"] = "gate"`.
   - Add `FLAGS_BY_ACTION["approve"] = {"reason"}` and `FLAGS_BY_ACTION["reject"] = {"reason"}`.
   - Keep the frozen-dataclass `WorkflowCommand` shape (`action`, `resource`, `params`) unchanged.
   - The parser must accept `/workflow approve gate`, `/workflow reject gate`, and `/workflow approve gate --reason "..."`; it must reject approve/reject with a resource other than `gate`, and reject unknown flags for those actions.

2. Create package `.github/scripts/resolve_config/sub_domains/gate_decision/` with:
   - `__init__.py` — follow the sibling sub_domains convention (one-line docstring, e.g. `"""Gate decision sub-domain package."""`).
   - `gate_decision.py` — a pydantic `BaseModel` named `GateDecision` with fields:
     - `decision: Literal["approved", "rejected"]`
     - `gate_step: str`
     - `approver: str`
     - `decided_at: str` (ISO-8601 UTC)
     - `reason: Optional[str] = None`
   - One-line docstring: immutable persisted record of a human's out-of-band gate decision used to route a held step deterministically.

3. Locate the `WorkflowState` model (`grep -rn "class WorkflowState" .github/scripts` — it lives at `.github/scripts/workflow_state/workflow_state.py`) and add:
   - `gate: Optional[GateDecision] = None`
   - `pending_gate_step: Optional[str] = None`
   Import `GateDecision` following the existing "domain entities first" import block convention. Verify old persisted states (JSON missing these keys) still load, and that `model_dump`/re-validate round-trips the new fields.

4. Extend `.github/scripts/resolve_config/sub_domains/workflow_command/test_workflow_command.py` with cases:
   - `approve gate` and `reject gate` with and without `--reason "..."`.
   - approve/reject with an invalid resource (not `gate`) is rejected.
   - unknown flag for approve/reject is rejected.

## Constraints

- Do not modify routing (`resolve_config.py` cascade) in this step.
- Keep the existing frozen-dataclass parser contract; only extend the registries.
- Follow the existing sub_domains package/`__init__.py` convention.

## Expected outcome

- Parser tests pass: `python -m pytest .github/scripts/resolve_config/sub_domains/workflow_command/test_workflow_command.py`.
- `WorkflowState` loads legacy states and round-trips the new `gate`/`pending_gate_step` fields.
- No other module import breaks (`python -c "import workflow_state"` equivalent from the scripts root).

## Relevant files

- `.github/scripts/resolve_config/sub_domains/workflow_command/workflow_command.py`
- `.github/scripts/resolve_config/sub_domains/workflow_command/test_workflow_command.py`
- `.github/scripts/resolve_config/sub_domains/workflow_command/__init__.py` (convention reference)
- `.github/scripts/workflow_state/workflow_state.py` (`WorkflowState`, import block, validators)
- `.github/scripts/workflow_state/sub_domains/dispatch/dispatch.py` (persisted-entity convention reference)

## Commit message

`[step 1/5] /workflow approve|reject gate grammar + GateDecision model + WorkflowState fields`
