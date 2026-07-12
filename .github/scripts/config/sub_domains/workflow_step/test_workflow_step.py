"""Tests for WorkflowStep."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import WorkflowCondition, WorkflowStep


class TestWorkflowStepSubWorkflow:
    """Test WorkflowStep with sub-workflow support."""

    def test_step_with_workflow_only(self):
        step = WorkflowStep(name="subtask", workflow="review-only")
        assert step.workflow == "review-only"
        assert step.agent is None

    def test_step_with_workflow_and_path(self):
        step = WorkflowStep(name="subtask", workflow="cve", path=".custom/cve.yaml")
        assert step.workflow == "cve"
        assert step.path == ".custom/cve.yaml"

    def test_step_missing_both_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            WorkflowStep(name="broken", next="foo")

    def test_step_has_both_raises(self):
        with pytest.raises(ValueError, match="both"):
            WorkflowStep(name="bad", agent="dev", workflow="review-only")


class TestWorkflowStep:
    """Test WorkflowStep Pydantic model."""

    def test_simple_step(self):
        step = WorkflowStep(name="analyze", agent="genius", next="context")
        assert step.name == "analyze"
        assert step.agent == "genius"
        assert step.next == "context"
        assert step.conditions is None

    def test_terminal_step(self):
        step = WorkflowStep(name="review", agent="review", next=None)
        assert step.next is None

    def test_step_with_conditions(self):
        conditions = [
            WorkflowCondition(on_result="needs_review", next="review"),
            WorkflowCondition(on_result="needs_revision", next="dev"),
            WorkflowCondition(on_result="default", next=None),
        ]
        step = WorkflowStep(
            name="implement", agent="dev", conditions=conditions
        )
        assert len(step.conditions) == 3
        assert step.conditions[0].on_result == "needs_review"

    def test_step_with_skills(self):
        step = WorkflowStep(name="dev", agent="dev", skill=["python"])
        assert step.skill == ["python"]

    def test_step_with_multiple_skills(self):
        step = WorkflowStep(name="dev", agent="dev", skill=["python", "rust"])
        assert step.skill == ["python", "rust"]

    def test_step_without_skills_defaults_to_none(self):
        step = WorkflowStep(name="dev", agent="dev")
        assert step.skill is None

    def test_human_gate_defaults_to_false(self):
        step = WorkflowStep(name="dev", agent="dev")
        assert step.human_gate is False

    def test_human_gate_can_be_true(self):
        step = WorkflowStep(name="spec_approval", agent="genius", human_gate=True)
        assert step.human_gate is True

    def test_human_gate_with_conditions(self):
        conditions = [
            WorkflowCondition(on_result="approved", next="implement"),
            WorkflowCondition(on_result="rejected", next="spec"),
        ]
        step = WorkflowStep(
            name="stub_approval",
            agent="genius",
            human_gate=True,
            conditions=conditions,
        )
        assert step.human_gate is True
        assert len(step.conditions) == 2
