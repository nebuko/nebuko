"""Workflow definition entity."""

from pydantic import BaseModel, Field, field_validator

from ..workflow.workflow_input_spec import WorkflowInputSpec
from ..workflow_step.workflow_step import WorkflowStep


class WorkflowDefinition(BaseModel):
    """A complete workflow definition.

    A workflow is a typed function (issue #813): ``inputs`` are its parameters
    and ``outputs`` are the values it returns to its caller. Both are optional
    and default to empty, so existing workflows keep their shape.

    Entry is derived from the definition itself: the first step's agent is the
    workflow's entry agent (issue #816). *When* a workflow runs is owned by
    GitHub CI, not by this model.
    """

    name: str
    description: str
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

    @property
    def entry_agent(self) -> str:
        """Agent of the first step; "" when the workflow has no steps.

        The first step is definitionally the workflow's entry point, so this
        is what ``/nebuko run workflow --name <wf>`` starts on (issue #816).
        An empty result preserves the fall-through-to-``@@mention`` semantics.
        """
        if not self.steps:
            return ""
        return self.steps[0].agent or ""
