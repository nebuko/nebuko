"""Workflow trigger entity."""

from pydantic import BaseModel


class WorkflowTrigger(BaseModel):
    """Which @@mentions can start this workflow."""

    agents: list[str]
