"""Tests for WorkflowCondition."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
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
