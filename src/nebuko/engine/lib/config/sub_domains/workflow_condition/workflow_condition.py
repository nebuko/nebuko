"""Workflow condition entity."""

from typing import Optional

from pydantic import BaseModel


class WorkflowCondition(BaseModel):
    """Conditional routing within a workflow step.

    Evaluates the turn outcome's ``route_key`` (the agent's ``appreciation``
    verdict) at the end of a step and routes to the matching ``next`` target.
    Conventionally used values for ``on_result`` are:

    - **approved** — Human (or model proxy) confirmed readiness to proceed.
    - **rejected** — Human requested changes; route back to an earlier step.
    - **default** — Fallback when no other condition matches. A verdict the
      step does not declare falls back to this edge, and to a benign ``null``
      finish (``exit 0``) when the step declares no ``default`` either
      (issue #1006).

    Custom values (e.g. ``needs_more_info``) are supported for workflow-specific
    branching. The semantic evaluation of "approved" vs "rejected" is the
    responsibility of the agent (typically the architect agent in approval steps),
    not the workflow engine.
    """

    # Conventional result values documented above.
    on_result: str
    next: Optional[str] = None
