"""Tests for WorkflowDefinition."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import WorkflowStep, WorkflowDefinition


class TestWorkflowDefinition:
    """Test WorkflowDefinition Pydantic model."""

    def test_valid_workflow(self):
        steps = [
            WorkflowStep(name="analyze", agent="architect", next="context"),
            WorkflowStep(name="context", agent="context", next=None),
        ]
        wf = WorkflowDefinition(
            name="test-workflow",
            description="A test workflow",
            steps=steps,
        )
        assert wf.name == "test-workflow"
        assert len(wf.steps) == 2

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                description="No name",
                steps=[],
            )

    def test_entry_agent_is_first_step_agent(self):
        wf = WorkflowDefinition(
            name="wf",
            description="d",
            steps=[
                WorkflowStep(name="analyze", agent="architect", next="apply"),
                WorkflowStep(name="apply", agent="dev", next=None),
            ],
        )
        assert wf.entry_agent == "architect"

    def test_entry_agent_empty_without_steps(self):
        wf = WorkflowDefinition(name="wf", description="d", steps=[])
        assert wf.entry_agent == ""

    def test_entry_agent_empty_for_sourceless_first_step(self):
        """A script first step carries no agent, so entry falls through."""
        wf = WorkflowDefinition(
            name="wf",
            description="d",
            steps=[
                WorkflowStep(
                    name="build",
                    script={"command": "scripts/build.py"},
                    next=None,
                )
            ],
        )
        assert wf.entry_agent == ""
