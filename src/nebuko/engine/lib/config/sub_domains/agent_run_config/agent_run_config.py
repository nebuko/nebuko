"""Canonical AGENT_RUN_CONFIG sentinel contract (issue #765).

One validated representation of a single ``AGENT_RUN_CONFIG`` payload, shared
by the output validator and the routing helpers. The previous contract lived
half in prose (the prompts) and half in a single validator check, which let
the exact dispatch-only sentinel ``context`` is instructed to emit fail validation
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

# Provenance of a dispatch decision (issue #864): distinguishes a decision the
# agent actually made from one the engine synthesized when the run ended with
# none. Consumers (routing, exit-code mapping, notices) can then treat the
# synthesized fallback differently from an agent-explicit ``hold``.
DispatchOrigin = Literal["agent", "engine_fallback"]


class DispatchConfig(BaseModel):
    """A workflow dispatch decision: how a single agent run ends."""

    status: DispatchStatus
    branch: Optional[str] = None
    complexity: Optional[str] = None
    reason: Optional[str] = None
    workflow: Optional[str] = None
    step: Optional[str] = None
    # ``agent`` for a decision the agent emitted; ``engine_fallback`` for the
    # synthesized decision-less default (issue #864).
    origin: DispatchOrigin = "agent"

    @classmethod
    def fallback_for(
        cls, callback: str = "", diagnostic: str = ""
    ) -> "DispatchConfig":
        """Synthesize the decision-less fallback (issue #864).

        An agent that ends a run without emitting any decision is contract
        drift, not a normal completion. When the run carries a **return
        address** (``callback``) — it was reached through a caller's
        ``function_call`` — the bounded continuation is to advance back to the
        caller, which re-enters with the report and decides. Only a rudderless
        run (no return address) holds for a human.

        Always ``origin='engine_fallback'`` and never sets an explicit
        destination (``task_hint``/``workflow``/``step``), so the callback gate
        in ``resolve_route._callback_return_target`` can carry it home.

        Args:
            callback: The caller to return to, or empty for a rudderless run.
            diagnostic: Optional parse-failure diagnostic folded into the reason.

        Returns:
            A ``DispatchConfig`` with status ``advance`` (return address
            present) or ``hold`` (absent), tagged ``engine_fallback``.
        """
        callback = (callback or "").strip()
        base = "no-action default: agent emitted no decision"
        if diagnostic:
            base += (
                " and the AGENT_RUN_CONFIG sentinel could not be parsed "
                f"({diagnostic})"
            )
        if callback:
            return cls(
                status="advance",
                reason=f"{base}; returning to caller '{callback}'",
                origin="engine_fallback",
            )
        return cls(status="hold", reason=base, origin="engine_fallback")

    @classmethod
    def no_action_default(cls, reason: str) -> "DispatchConfig":
        """Retained for #765 callers — a rudderless no-action fallback.

        Equivalent to :meth:`fallback_for` with no return address (always
        ``hold`` + ``origin='engine_fallback'``), but the caller supplies the
        exact reason text.
        """
        return cls(status="hold", reason=reason, origin="engine_fallback")


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
