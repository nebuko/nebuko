from typing import Optional

from pydantic import BaseModel, Field, model_validator

from config import StepRef
from complexity_governor.complexity_governor import (
    ComplexityAdjustment,
    ComplexityReviewRequest,
)

from ..dispatch.dispatch import Dispatch
from ..state_metrics.state_metrics import StateMetrics

class HistoryEntry(BaseModel):
    """A summarized record of one workflow step."""

    step: str
    agent: str
    summary: str = ""
    # Full flat step path (outermost first, last = executing leaf) at the
    # moment this step ran (issue #638). Makes the history a replayable
    # step-path log: after every run history[-1].position == WorkflowState.position.
    position: list[StepRef] = Field(default_factory=list)
    # Routing decision emitted by THIS step (issue #685). Reuses the top-level
    # Dispatch model so the ordered history becomes a self-contained,
    # replayable movement log. None for legacy snapshots that predate the field.
    dispatch: Optional[Dispatch] = None
    # GitHub comment IDs of this step's output. Multi-part agent messages are
    # posted as separate comments, so an entry may reference several IDs
    # (issue #504). Legacy snapshots store a scalar `comment_id`, coerced
    # by the validator below.
    comment_ids: list[int] = Field(default_factory=list)
    inject_context: bool = True
    artifacts: list[str] = Field(default_factory=list)
    pin: bool = False
    result: Optional[str] = None
    # Metrics of THIS step's agent invocation. This is the single metrics
    # authority (issue #792): the former top-level ``WorkflowState.metrics``
    # snapshot was removed so each step keeps its own record.
    metrics: Optional[StateMetrics] = None  # @stub-protected
    # Complexity decision applied to THIS step (issue #882): the governor's
    # clamped adjustment over the base tier, with the evidence snapshot that
    # justified it. None when the step ran at its base tier unchanged.
    complexity_adjustment: Optional[ComplexityAdjustment] = None  # @stub-protected
    # Agent-issued complexity review request recorded during THIS step
    # (issue #882); adjudicated by the governor at the next boundary.
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
