"""Tests for TriggerCommands value object."""

import sys
from pathlib import Path

import pytest

from nebuko.engine.lib.resolve_config.sub_domains.trigger_commands.trigger_commands import (  # noqa: E402
    TriggerCommands,
)
from nebuko.engine.lib.resolve_config.sub_domains.workflow_command.workflow_command import (  # noqa: E402
    WorkflowCommand,
)


class TestTriggerCommands:
    """Test the TriggerCommands frozen dataclass."""

    def test_all_fields_set(self):
        tc = TriggerCommands(
            is_bot=True,
            mention_agent="dev",
            workflow_command=WorkflowCommand(
                action="run", resource="workflow", params={"name": "default"}
            ),
            debug=True,
        )
        assert tc.is_bot is True
        assert tc.mention_agent == "dev"
        assert tc.workflow_command.action == "run"
        assert tc.workflow_command.resource == "workflow"
        assert tc.workflow_command.params == {"name": "default"}
        assert tc.debug is True

    def test_defaults_none(self):
        tc = TriggerCommands(
            is_bot=False,
            mention_agent=None,
            workflow_command=None,
            debug=False,
        )
        assert tc.mention_agent is None
        assert tc.workflow_command is None

    def test_frozen(self):
        tc = TriggerCommands(
            is_bot=False,
            mention_agent=None,
            workflow_command=None,
            debug=False,
        )
        with pytest.raises(Exception):
            tc.is_bot = True  # type: ignore[misc]