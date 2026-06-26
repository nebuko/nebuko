"""Tests for WorkflowTrigger."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import WorkflowTrigger


class TestWorkflowTrigger:
    """Test WorkflowTrigger Pydantic model."""

    def test_valid_trigger(self):
        trigger = WorkflowTrigger(agents=["genius"])
        assert trigger.agents == ["genius"]

    def test_multiple_trigger_agents(self):
        trigger = WorkflowTrigger(agents=["genius", "review"])
        assert trigger.agents == ["genius", "review"]
