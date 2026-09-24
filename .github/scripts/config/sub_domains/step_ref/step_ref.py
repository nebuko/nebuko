"""Workflow step reference entity (issue #638).

A ``StepRef`` is a single ``(workflow, step)`` address element of the
canonical workflow position — the ordered flat step path
(``WorkflowPosition.refs``), outermost first, whose last element is the
step actually executing.
"""

from pydantic import BaseModel


class StepRef(BaseModel):
    """One element of the workflow step path (issue #638).

    Attributes:
        workflow: Workflow file name owning ``step``.
        step: Step name within ``workflow``.
    """

    workflow: str
    step: str
