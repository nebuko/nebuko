"""Non-routing run preferences carried alongside a turn outcome (issue #964).

These knobs used to live on the legacy ``Dispatch`` model, which conflated
routing (``status``/``branch``) with runtime preferences (sampling parameters,
model override, pinning). Routing is now derived exclusively from
:class:`~..turn_outcome.turn_outcome.TurnOutcome`; everything here is inert
with respect to *where* the workflow goes — it only shapes *how* the next
agent runs.
"""

from typing import Optional

from pydantic import BaseModel


class RunPrefs(BaseModel):
    """Runtime preferences for the run that consumes the turn outcome."""

    # "" = the agent declared no tier; the resolver applies the workflow step's
    # declared tier, else "medium" (issue #787).
    complexity: str = ""
    model: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    min_p: Optional[float] = None
    presence_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    skills: Optional[str] = None
    pin: bool = False
    debug: bool = False


__all__ = ["RunPrefs"]
