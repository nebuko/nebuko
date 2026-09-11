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

    def test_prompt_parses_plain_string(self):
        step = WorkflowStep(name="analyze", agent="genius", prompt="Be concise.")
        assert step.prompt == "Be concise."

    def test_prompt_parses_multiline_block_verbatim(self):
        step = WorkflowStep(
            name="analyze",
            agent="genius",
            prompt=(
                "Analyze the transcription and provide me:\n"
                "a short summary (~5-10 sentences)\n"
                "5 Key points\n"
            ),
        )
        assert step.prompt == (
            "Analyze the transcription and provide me:\n"
            "a short summary (~5-10 sentences)\n"
            "5 Key points\n"
        )

    def test_prompt_defaults_to_none(self):
        step = WorkflowStep(name="dev", agent="dev")
        assert step.prompt is None

    def test_prompt_coexists_with_existing_fields(self):
        step = WorkflowStep(
            name="stub",
            agent="dev",
            skill=["stub-only"],
            human_gate=True,
            next="stub_review",
            prompt="Fill in the stubs only.",
        )
        assert step.prompt == "Fill in the stubs only."
        assert step.skill == ["stub-only"]
        assert step.human_gate is True
        assert step.next == "stub_review"

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

    def test_human_gate_step_with_next_and_conditions_is_valid(self):
        # Guard: a gated step may define both `next` and `conditions`.
        # `next` acts as fallback routing when the agent reports a branch no
        # condition matches (issue #506 review dispute over this combo).
        conditions = [
            WorkflowCondition(on_result="approved", next="implement"),
            WorkflowCondition(on_result="rejected", next="stub"),
        ]
        step = WorkflowStep(
            name="stub_approval",
            agent="genius",
            human_gate=True,
            conditions=conditions,
            next="implement",
        )
        assert step.human_gate is True
        assert step.next == "implement"
        assert len(step.conditions) == 2
