"""Tests for WorkflowDefinition."""

import pytest
from pydantic import ValidationError

from config import WorkflowTrigger, WorkflowStep, WorkflowDefinition


class TestWorkflowDefinition:
    """Test WorkflowDefinition Pydantic model."""

    def test_valid_workflow(self):
        trigger = WorkflowTrigger(agents=["genius"])
        steps = [
            WorkflowStep(name="analyze", agent="genius", next="context"),
            WorkflowStep(name="context", agent="ctx", next=None),
        ]
        wf = WorkflowDefinition(
            name="test-workflow",
            description="A test workflow",
            trigger=trigger,
            steps=steps,
        )
        assert wf.name == "test-workflow"
        assert len(wf.steps) == 2
        assert wf.trigger.agents == ["genius"]

    def test_missing_name_raises(self):
        trigger = WorkflowTrigger(agents=["genius"])
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                description="No name",
                trigger=trigger,
                steps=[],
            )
