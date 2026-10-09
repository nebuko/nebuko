"""Workflow definition entity."""

from pydantic import BaseModel, Field, field_validator

from ..workflow.workflow_input_spec import WorkflowInputSpec
from ..workflow_step.workflow_step import WorkflowStep
from ..workflow_trigger.workflow_trigger import WorkflowTrigger


class WorkflowDefinition(BaseModel):
    """A complete workflow definition.

    A workflow is a typed function (issue #813): ``inputs`` are its parameters
    and ``outputs`` are the values it returns to its caller. Both are optional
    and default to empty, so existing workflows keep their shape.
    """

    name: str
    description: str
    trigger: WorkflowTrigger
    steps: list[WorkflowStep]
    # Declared typed inputs (parameters). @stub-protected
    inputs: list[WorkflowInputSpec] = Field(default_factory=list)
    # Declared outputs (return values). A bare string coerces to a one-element
    # list for convenience. @stub-protected
    outputs: list[str] = Field(default_factory=list)

    @field_validator("outputs", mode="before")
    @classmethod
    def _coerce_outputs(cls, value: object) -> object:
        if isinstance(value, str):
            return [value]
        return value
