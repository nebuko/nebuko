"""Regression guard: the caller workflow hard-gates who can start a run (issue #1024).

The bundled caller (``.github/workflows/interntal-trigger-nebuko-on-comment.yaml``)
invokes the reusable workflow with the repo's secrets. Its job-level ``if:`` is
the only place a denial can happen *before* any run starts, so it must carry an
event-aware, fail-closed gate: on ``issue_comment`` the comment author must be
``OWNER``/``MEMBER``/``COLLABORATOR`` and not the bot; on ``issues`` the same
allow-list applies to the issue author. The reusable workflow additionally
refuses the bot's own comments so continuation markers and gate commands
authored by ``nebuko[bot]`` stay inert.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CALLER_YAML = REPO_ROOT / ".github" / "workflows" / "interntal-trigger-nebuko-on-comment.yaml"
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"

ALLOWED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
BOT_LOGIN = "nebuko[bot]"


def _caller() -> dict:
    return yaml.safe_load(CALLER_YAML.read_text(encoding="utf-8"))


def _caller_job() -> dict:
    return _caller()["jobs"]["agent"]


def _caller_if() -> str:
    return _caller_job()["if"]


def _run_task_if() -> str:
    workflow = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    return workflow["jobs"]["run-task"]["if"]


class TestCallerTriggers:
    def test_triggers_on_created_comments_only(self):
        # PyYAML parses the bare `on:` key as boolean True (YAML 1.1).
        on = _caller()[True]

        assert on["issue_comment"] == {"types": ["created"]}
        assert on["issues"] == {"types": ["closed"]}

    def test_caller_declares_id_token_write(self):
        assert _caller()["permissions"]["id-token"] == "write"


class TestCallerGate:
    def test_job_carries_an_if_gate(self):
        assert _caller_if().strip(), "the caller job must gate before any run starts"

    def test_gate_is_event_aware(self):
        expression = _caller_if()

        assert "github.event_name == 'issue_comment'" in expression
        assert "github.event_name == 'issues'" in expression

    def test_gate_allows_only_trusted_associations_on_comments(self):
        expression = _caller_if()

        for association in ALLOWED_ASSOCIATIONS:
            assert association in expression
        assert "github.event.comment.author_association" in expression

    def test_gate_allows_only_trusted_associations_on_issues(self):
        expression = _caller_if()

        assert "github.event.issue.author_association" in expression

    def test_gate_excludes_the_bot_on_comments(self):
        assert f"github.event.comment.user.login != '{BOT_LOGIN}'" in _caller_if()

    def test_gate_excludes_the_bot_on_issues(self):
        assert f"github.event.issue.user.login != '{BOT_LOGIN}'" in _caller_if()

    def test_gate_is_fail_closed(self):
        """No negated or defaulted association check may open the gate."""
        expression = _caller_if()

        assert "inputs.CALLER_AUTH" not in expression
        assert "|| github.event.comment.author_association" not in expression
        assert "|| github.event.issue.author_association" not in expression


class TestReusableWorkflowBotGuard:
    def test_issue_comment_branch_refuses_bot_comments(self):
        expression = _run_task_if()

        assert f"github.event.comment.user.login != '{BOT_LOGIN}'" in expression

    def test_bot_conjunct_precedes_the_body_marker_checks(self):
        """The bot exclusion must sit before the body-marker arms it guards."""
        expression = _run_task_if()
        comment_branch = expression.split("github.event_name == 'issue_comment'", 1)[1]

        bot_pos = comment_branch.find(f"github.event.comment.user.login != '{BOT_LOGIN}'")
        marker_pos = comment_branch.find("<!-- bot-answer-continuation -->")

        assert bot_pos != -1
        assert marker_pos != -1
        assert bot_pos < marker_pos

    def test_issues_branch_is_untouched(self):
        """The issues arm keeps its original shape (association + body markers)."""
        expression = _run_task_if()
        issues_branch = expression.split("github.event_name == 'issues'", 1)[1].split(
            "github.event_name == 'issue_comment'", 1
        )[0]

        assert "github.event.issue.author_association" in issues_branch
        assert f"github.event.issue.user.login != '{BOT_LOGIN}'" not in issues_branch
