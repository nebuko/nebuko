"""Persisted step-input record (issue #872).

One resolved entry of a step's declared ``inputs:`` wiring, captured so the
deterministic input/output contract lives in workflow state alongside the step
that consumed it (auditable, and usable as context by later steps).
"""

from pydantic import BaseModel, ConfigDict

__all__ = ["StepInputRecord", "MAX_VALUE_CHARS"]

# Captured values are stored in the state snapshot (re-written into a comment
# every step), so a hard cap keeps a large deterministic output from ballooning
# the ledger; the truncation is flagged rather than silent.
MAX_VALUE_CHARS = 2000


class StepInputRecord(BaseModel):  # @stub-protected
    """Immutable record of one materialized step input.

    Fields:
        name: Declared input name.
        binding: Binding kind that produced the value (``exec``/``str``/``ref``).
        ok: Whether materialization succeeded.
        detail: Failure detail when ``ok`` is False (empty otherwise).
        value: Captured value, truncated to :data:`MAX_VALUE_CHARS`.
        truncated: True when ``value`` was truncated for storage.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    binding: str = ""
    ok: bool = True
    detail: str = ""
    value: str = ""
    truncated: bool = False
