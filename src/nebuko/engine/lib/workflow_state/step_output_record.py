"""Declared step outputs are step comments (issue #756, ADR-0013).

A step's output *is* the comment it posts. This record is the locator that
points at that comment — ``{step, comment_id}`` — stored under
``current_context["step_outputs"]`` in the WORKFLOW_STATE v1 JSON.

The comment body itself is deliberately **not** stored here: the state block is
re-embedded into every posted comment, so persisting bodies would compound
against GitHub's comment-size limit. Resolution reads the body from the bounded
thread read instead (see ``features/resolve_step_output``).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, field_validator

#: ``current_context`` key holding the list of step-output locators.
STEP_OUTPUTS_KEY = "step_outputs"


class StepOutputRecord(BaseModel):
    """Locator for one step's output comment: step name -> comment id."""

    step: str
    comment_id: Optional[str] = None
    permalink: Optional[str] = None
    anchor: Optional[str] = None
    posted_at: Optional[str] = None

    @field_validator("comment_id", mode="before")
    @classmethod
    def _coerce_comment_id(cls, value: object) -> object:
        # GitHub comment ids are integers; the locator stores a string so a
        # URL (the adapter's ``post`` return) and a numeric id share one type.
        if value is None:
            return None
        return str(value)

    def upsert_into(self, current_context: dict[str, Any]) -> None:
        """Record this locator under ``step_outputs``; latest write wins.

        Uniqueness is on ``step``: an existing entry for the same step is
        replaced (a loop re-run re-points the locator at the newest comment).
        """
        records = [r for r in self.load_all(current_context) if r.step != self.step]
        records.append(self)
        current_context[STEP_OUTPUTS_KEY] = [r.model_dump() for r in records]

    @classmethod
    def load_all(cls, current_context: dict[str, Any]) -> list["StepOutputRecord"]:
        """Parse the stored locators, skipping malformed entries."""
        raw = (current_context or {}).get(STEP_OUTPUTS_KEY)
        if not isinstance(raw, list):
            return []
        records: list[StepOutputRecord] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                records.append(cls.model_validate(item))
            except Exception:  # noqa: BLE001 - a malformed locator is not fatal
                continue
        return records

    @classmethod
    def index(cls, current_context: dict[str, Any]) -> dict[str, "StepOutputRecord"]:
        """The locators keyed by step name (latest record wins)."""
        return {record.step: record for record in cls.load_all(current_context)}
