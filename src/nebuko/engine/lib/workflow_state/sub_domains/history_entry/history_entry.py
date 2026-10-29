from typing import Optional

from pydantic import BaseModel, Field, model_validator

from nebuko.engine.lib.config import StepRef
from nebuko.engine.lib.complexity_governor.complexity_governor import (
    ComplexityAdjustment,
    ComplexityReviewRequest,
)

from ..run_prefs.run_prefs import RunPrefs
from ..state_metrics.state_metrics import StateMetrics
from ..step_input_record.step_input_record import StepInputRecord
from ..turn_outcome.turn_outcome import TurnOutcome


class HistoryEntry(BaseModel):
    """A summarized record of one workflow step."""

    step: str
    agent: str
    summary: str = ""
    # Full flat step path (outermost first, last = executing leaf) at the
    # moment this step ran (issue #638). Makes the history a replayable
    # step-path log: after every run history[-1].position == WorkflowState.position.
    position: list[StepRef] = Field(default_factory=list)
    # Factual outcome of THIS step's turn (issue #964): engine-measured
    # completion + the agent's verdict, from which routing is derived. Every
    # entry is a routing fact, so the outcome is required.
    turn_outcome: TurnOutcome  # @stub-protected
    # Non-routing run preferences this step asked for (issue #964).
    prefs: Optional[RunPrefs] = None  # @stub-protected
    # GitHub comment IDs of this step's output. Multi-part agent messages are
    # posted as separate comments, so an entry may reference several IDs
    # (issue #504). Legacy snapshots store a scalar `comment_id`, coerced
    # by the validator below.
    comment_ids: list[int] = Field(default_factory=list)
    inject_context: bool = True
    artifacts: list[str] = Field(default_factory=list)
    pin: bool = False
    result: Optional[str] = None
    # Deterministically materialized step inputs of THIS step (issue #872): the
    # resolved form of the step's declared ``inputs:`` wiring, persisted so the
    # value the agent was handed is auditable in workflow state. Empty for steps
    # that declare no inputs and for legacy snapshots.
    inputs: list[StepInputRecord] = Field(default_factory=list)
    # Metrics of THIS step's agent invocation. This is the single metrics
    # authority (issue #792): the former top-level ``WorkflowState.metrics``
    # snapshot was removed so each step keeps its own record.
    metrics: Optional[StateMetrics] = None  # @stub-protected
    # Complexity decision applied to THIS step (issue #869): the governor's
    # clamped adjustment over the base tier, with the evidence snapshot that
    # justified it. None when the step ran at its base tier unchanged.
    complexity_adjustment: Optional[ComplexityAdjustment] = None  # @stub-protected
    # Agent-issued complexity review request recorded during THIS step
    # (issue #869); adjudicated by the governor at the next boundary.
    complexity_review_request: Optional[ComplexityReviewRequest] = None  # @stub-protected

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_comment_id(cls, data):
        """Normalize historical scalar shape into the comment_ids list.

        Handles state snapshots embedded in already-posted comments:
        ``comment_id: int`` -> ``[int]``, ``null``/absent -> ``[]``.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        legacy = data.pop("comment_id", None)
        ids = data.get("comment_ids")
        if ids is None:
            ids = []
        if legacy is not None:
            ids = [*ids, legacy]
        data["comment_ids"] = ids
        return data
