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


_STILL_DENIED = [
    "git checkout main",
    "git checkout -B feature/x origin/main",
    "git -C repo checkout main",
    "git switch -c feature/x",
    "git branch -D feature/x",
    "git branch -m old new",
    "git branch --delete feature/x",
    "git branch -f feature/x HEAD~1",
    "git branch --set-upstream-to=origin/x",
    "git branch --list x -d y",
    "git status && git checkout main",
]


@pytest.mark.parametrize("command", _STILL_DENIED)
def test_branch_mutating_forms_stay_denied(command):
    assert push_deny_list().check(command) is not None


_READ_ONLY = [
    "git branch",
    "git branch --show-current",
    "git branch -a",
    "git branch -r",
    "git branch -vv",
    "git branch --list 'feature/*'",
    "git branch -l feat*",
    "git branch --contains abc123",
    "git branch --merged origin/main",
    "git branch -a | head",
    "git checkout -- src/app.ts",
    "git checkout HEAD -- a.txt b.txt",
    "git checkout origin/main -- docs/",
    "git log --oneline origin/feature/x",
]


@pytest.mark.parametrize("command", _READ_ONLY)
def test_read_only_branch_and_restore_forms_are_allowed(command):
    """Issue #1268: denying ``git branch --show-current`` hid from the agent
    that its session was on ``main``."""
    assert push_deny_list().check(command) is None
