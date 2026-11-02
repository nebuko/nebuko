"""Command deny list: the agent's branch-mutating surface (issue #1252).

Run 41354010528: ``git checkout -b`` is permitted, so the agent switched
branch mid-session and finalize stranded the scaffolded branch. These tests
pin the fix contract: branch-mutating commands are denied by the
``git-branch-switch`` rule (run 41354010528).
"""

from __future__ import annotations

import pytest

from nebuko.engine.lib.agent_script.features.terminal_batch_tool.command_deny_list import (
    push_deny_list,
)

_CHECKOUT_CMDS = [
    "git checkout -b feature/x",
    "git switch feature/x",
    "git branch feature/x",
]


@pytest.mark.parametrize("command", _CHECKOUT_CMDS)
def test_git_checkout_and_switch_are_denied_for_agents(command):
    """Red fix-contract: branch-mutating commands must be denied.

    Pins the follow-up fix: ``CHECKOUT_PATTERN`` and
    ``CHECKOUT_DENY_MESSAGE`` in ``command_deny_list.py``, wired into
    ``push_deny_list()``, with a message naming the scaffolded-branch rule —
    same style as ``PUSH_DENY_MESSAGE``.
    """
    result = push_deny_list().check(command)

    assert result is not None
    assert "scaffolded branch" in result.message
