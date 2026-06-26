"""Tests for WorkflowCondition."""

from config import WorkflowCondition


class TestWorkflowCondition:
    """Test WorkflowCondition Pydantic model."""

    def test_valid_condition(self):
        cond = WorkflowCondition(on_result="needs_review", next="review")
        assert cond.on_result == "needs_review"
        assert cond.next == "review"

    def test_condition_without_next(self):
        cond = WorkflowCondition(on_result="default")
        assert cond.on_result == "default"
        assert cond.next is None
