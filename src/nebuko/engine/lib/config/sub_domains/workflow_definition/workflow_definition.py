"""Workflow definition entity."""

from pydantic import BaseModel

from ..workflow_step.workflow_step import WorkflowStep
from ..workflow_trigger.workflow_trigger import WorkflowTrigger


class WorkflowDefinition(BaseModel):
    """A complete workflow definition."""

    name: str
    description: str
    trigger: WorkflowTrigger
    steps: list[WorkflowStep]
