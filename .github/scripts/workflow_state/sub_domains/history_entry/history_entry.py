from typing import Optional

from pydantic import BaseModel, Field, model_validator

from config import StepRef

class HistoryEntry(BaseModel):
    """A summarized record of one workflow step."""

    step: str
    agent: str
    summary: str = ""
    # Full flat step path (outermost first, last = executing leaf) at the
    # moment this step ran (issue #638). Makes the history a replayable
    # step-path log: after every run history[-1].position == WorkflowState.position.
    position: list[StepRef] = Field(default_factory=list)
    # GitHub comment IDs of this step's output. Multi-part agent messages are
    # posted as separate comments, so an entry may reference several IDs
    # (issue #504). Legacy snapshots store a scalar `comment_id`, coerced
    # by the validator below.
    comment_ids: list[int] = Field(default_factory=list)
    inject_context: bool = True
    artifacts: list[str] = Field(default_factory=list)
    pin: bool = False
    result: Optional[str] = None

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
