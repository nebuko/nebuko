"""Gate verdict routing at a gated step (issue #1274).

RED specs: a rejected agent verdict at a ``human_gate`` step that declares an
``on_result: rejected`` conditional edge must auto-route that edge (including
backward edges) instead of parking in ``human_gate_hold``. Approved verdicts
and gates with no rejected edge keep the existing hold behavior.

These tests fail against the stub skeletons (NotImplementedError).
"""

from pathlib import Path

import pytest

from nebuko.engine.lib.resolve_config.features.resolve_route import (
    resolve_route as rr,
)
from nebuko.engine.lib.workflow_state import (
    GateLock,
    GateStatus,
)
from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

GATED_WF = """\
name: gated
description: plan -> implement with a human gate on plan
steps:
  - name: context
    agent: context
    next: plan
  - name: plan
    agent: architect
    human_gate: true
    next: implement
    conditions:
      - on_result: approved
        next: implement
      - on_result: rejected
        next: context
  -
"""


def _gated_state(appreciation: str, workflow: str = "gated") -> WorkflowState:
    return WorkflowModel.model_validate(
        {
            "version": 7,
            "workflow": workflow,
            "step": "plan",
            "agent": "corrupted",
            "started_by": "olivier-norture",
            "gate": {"step": "plan", "status": "armed"},
            "turn_outcome": {"status": "completed", "appreciation": appreciation},
        }
    )
