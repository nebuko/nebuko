from typing import Optional

from pydantic import BaseModel, model_validator


class ParentRef(BaseModel):
    """Pointer to the parent workflow's continuation point (issue #613).

    Replaces the name-only parent tracking with a machine-readable pointer:
    when a sub-workflow completes, the engine reads this block to know which
    parent flow (and which plan step) to resume.

    Fields:
        workflow: Parent workflow name (e.g. "plan-orchestration").
        step: Parent step that mounted this sub-workflow (e.g. "sub_impl").
        issue_number: Issue number the parent flow runs on.
        plan_step_index: 1-based index of the plan step this sub-workflow
            implements.
        plan_total_steps: Total number of steps in the plan.
    """

    workflow: str = ""
    step: str = ""
    issue_number: Optional[int] = None
    plan_step_index: Optional[int] = None
    plan_total_steps: Optional[int] = None

    @model_validator(mode="after")
    def validate_pointer_consistency(self) -> "ParentRef":
        # A partial pointer (issue/plan fields without the workflow+step
        # target) would resume the parent flow without a continuation point.
        if (self.issue_number is not None or self.plan_step_index is not None
                or self.plan_total_steps is not None) and (
                not self.workflow or not self.step
        ):
            raise ValueError(
                "parent pointer is incomplete: issue_number/plan_step_index/"
                "plan_total_steps require both workflow and step"
            )
        if (
            self.issue_number is not None
            or self.plan_step_index is not None
            or self.plan_total_steps is not None
        ):
            for label, value in (
                ("issue_number", self.issue_number),
                ("plan_step_index", self.plan_step_index),
                ("plan_total_steps", self.plan_total_steps),
            ):
                if value is not None and value < 1:
                    raise ValueError(
                        f"parent.{label} must be >= 1, got {value}"
                    )
        return self

    def is_empty(self) -> bool:
        """True when the block carries no parent pointer at all."""
        return not self.workflow and not self.step
