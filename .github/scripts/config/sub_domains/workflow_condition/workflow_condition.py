"""Workflow condition entity."""

from typing import Optional

from pydantic import BaseModel


class WorkflowCondition(BaseModel):
    """Conditional routing within a workflow step."""

    on_result: str
    next: Optional[str] = None
