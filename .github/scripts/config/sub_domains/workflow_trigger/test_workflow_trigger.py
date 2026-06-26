"""Tests for WorkflowTrigger."""

from config import WorkflowTrigger


class TestWorkflowTrigger:
    """Test WorkflowTrigger Pydantic model."""

    def test_valid_trigger(self):
        trigger = WorkflowTrigger(agents=["genius"])
        assert trigger.agents == ["genius"]

    def test_multiple_trigger_agents(self):
        trigger = WorkflowTrigger(agents=["genius", "review"])
        assert trigger.agents == ["genius", "review"]
