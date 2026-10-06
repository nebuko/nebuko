"""Canonical AGENT_RUN_CONFIG sentinel contract (issue #765).

One validated representation of a single ``AGENT_RUN_CONFIG`` payload, shared
by the output validator and the routing helpers. The previous contract lived
half in prose (the prompts) and half in a single validator check, which let
the exact dispatch-only sentinel ``ctx`` is instructed to emit fail validation
(defect D1 of issue #765).

Invariants encoded here:
    1. At least one action is present: ``task``, ``dispatch`` or
       ``function_call``.
    2. ``task`` (legacy workflow-relative selection) is never combined with
       ``function_call`` (imperative hand-off) — the legacy ambiguity guard.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, model_validator

from workflow_state.sub_domains.dispatch.dispatch import DISPATCH_STATUSES
from workflow_state.sub_domains.function_call.function_call import FunctionCall

# Kept in sync with the ``Dispatch`` model's accepted statuses so a valid
# dispatch payload is never rejected by the sentinel model.
DispatchStatus = Literal["advance", "loop", "hold", "terminate", "soft_kill"]

assert set(DispatchStatus.__args__) == set(DISPATCH_STATUSES)


class DispatchConfig(BaseModel):
    """A workflow dispatch decision: how a single agent run ends."""

    status: DispatchStatus
    branch: Optional[str] = None
    complexity: Optional[str] = None
    reason: Optional[str] = None
    workflow: Optional[str] = None
    step: Optional[str] = None

    @classmethod
    def no_action_default(cls, reason: str) -> "DispatchConfig":
        """Synthesize the safe no-action fallback (``status='hold'``).

        An agent that ends a run without emitting any decision is a contract
        drift, not a normal completion. The safe response is to hold for a
        human (never auto-advance an unknown state) and record why.
        """
        return cls(status="hold", reason=reason)


class AgentRunConfig(BaseModel):
    """Canonical, validated representation of one AGENT_RUN_CONFIG sentinel."""

    task: Optional[str] = None  # legacy workflow-relative selection
    dispatch: Optional[DispatchConfig] = None
    function_call: Optional[FunctionCall] = None

    @model_validator(mode="after")
    def validate_at_least_one_action(self) -> "AgentRunConfig":
        if self.task is None and self.dispatch is None and self.function_call is None:
            raise ValueError(
                "AGENT_RUN_CONFIG missing required field: at least one of "
                "'task', 'dispatch' or 'function_call' is required"
            )
        return self

    @model_validator(mode="after")
    def validate_task_not_with_function_call(self) -> "AgentRunConfig":
        if self.task is not None and self.function_call is not None:
            raise ValueError(
                "AGENT_RUN_CONFIG cannot combine 'task' with 'function_call'"
            )
        return self
