"""Workflow condition entity."""

from typing import Optional

from pydantic import BaseModel


class WorkflowCondition(BaseModel):
    """Conditional routing within a workflow step.

    Evaluates the ``dispatch.branch`` value emitted by the agent at the end
    of a step and routes to the matching ``next`` target. Conventionally used
    values for ``on_result`` are:

    - **approved** — Human (or model proxy) confirmed readiness to proceed.
    - **rejected** — Human requested changes; route back to an earlier step.
    - **default** — Fallback when no other condition matches.

    Custom values (e.g. ``needs_more_info``) are supported for workflow-specific
    branching. The semantic evaluation of "approved" vs "rejected" is the
    responsibility of the agent (typically the genius agent in approval steps),
    not the workflow engine.
    """

    # Conventional result values documented above.
    on_result: str
    next: Optional[str] = None
