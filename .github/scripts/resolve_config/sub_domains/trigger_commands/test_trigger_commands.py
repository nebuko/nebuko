"""Tests for TriggerCommands value object."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.sub_domains.trigger_commands.trigger_commands import (  # noqa: E402
    TriggerCommands,
)


class TestTriggerCommands:
    """Test the TriggerCommands frozen dataclass."""

    def test_all_fields_set(self):
        tc = TriggerCommands(
            is_bot=True,
            mention_agent="dev",
            workflow_cmd="default",
            force_next=("force-next-step", "implement"),
            resume="fix the bug",
            debug=True,
        )
        assert tc.is_bot is True
        assert tc.mention_agent == "dev"
        assert tc.workflow_cmd == "default"
        assert tc.force_next == ("force-next-step", "implement")
        assert tc.resume == "fix the bug"
        assert tc.debug is True

    def test_defaults_none(self):
        tc = TriggerCommands(
            is_bot=False,
            mention_agent=None,
            workflow_cmd=None,
            force_next=None,
            resume=None,
            debug=False,
        )
        assert tc.mention_agent is None
        assert tc.workflow_cmd is None
        assert tc.force_next is None
        assert tc.resume is None

    def test_frozen(self):
        tc = TriggerCommands(
            is_bot=False,
            mention_agent=None,
            workflow_cmd=None,
            force_next=None,
            resume=None,
            debug=False,
        )
        with pytest.raises(Exception):
            tc.is_bot = True  # type: ignore[misc]