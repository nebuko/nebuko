"""Tests for the CALLER_AUTH trigger gate in openhands-multi-agent.yml.

The gate lives in the workflow `if:` expression (GitHub Expression Language,
not directly evaluable here), so this module carries a pure reference model
that mirrors the expression and a table-driven test of its semantics:
`inputs.CALLER_AUTH == 'off' || contains(fromJSON('[...]'), author_association)`.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"

ALLOWED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
# Values GitHub can emit that must NOT trigger the workflow when CALLER_AUTH=on
DENIED_ASSOCIATIONS = ("NONE", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN", None)

ISSUE_BODY = "Hello @@architect please analyze"
MACHINE_BODY = "<!-- workflow-continue -->"
BOT_BODY = "<!-- bot-agent-dev -->done"


def caller_authed(author_association, caller_auth):
    """Reference model of the caller-auth clause in the `if:` expression."""
    if caller_auth == "off":
        return True
    return author_association in ALLOWED_ASSOCIATIONS


def matches_existing_gates(body, event_name):
    """Reference model of the pre-existing body/marker gates (unchanged by #597)."""
    if body.startswith("<!-- bot-agent-") or body.startswith("<!-- bot-workflow-terminal -->"):
        return False
    if event_name == "issue_comment" and (
        '"function_call"' in body
        or "<!-- workflow-continue -->" in body
        or "<!-- bot-subtask-complete" in body
        or "<!-- bot-trigger:" in body
    ):
        return True
    return "@@architect" in body or "/nebuko" in body


def trigger_starts(event_name, author_association, caller_auth, body=ISSUE_BODY, action="opened"):
    """Reference model of the full job-level `if:` expression."""
    if action == "closed":
        return False
    if event_name == "workflow_call":
        return True  # association check does not apply to workflow_call
    if event_name not in ("issues", "issue_comment"):
        return False
    return caller_authed(author_association, caller_auth) and matches_existing_gates(body, event_name)


class TestCallerAuthClause:
    """The association clause alone, for each CALLER_AUTH setting."""

    def test_off_allows_every_association(self):
        for association in ALLOWED_ASSOCIATIONS + DENIED_ASSOCIATIONS:
            assert caller_authed(association, "off") is True

    def test_on_allows_whitelisted_associations(self):
        for association in ALLOWED_ASSOCIATIONS:
            assert caller_authed(association, "on") is True

    def test_on_denies_outsider_associations(self):
        for association in DENIED_ASSOCIATIONS:
            assert caller_authed(association, "on") is False


class TestTriggerMatrix:
    """Table-driven: (event_type, author_association, CALLER_AUTH) -> start/skip."""

    @pytest.mark.parametrize(
        "event_name, association, caller_auth, expected",
        [
            # CALLER_AUTH=off: existing permissive behavior preserved
            ("issues", "NONE", "off", True),
            ("issues", "OWNER", "off", True),
            ("issue_comment", "FIRST_TIMER", "off", True),
            # CALLER_AUTH=on: only whitelisted associations start the job
            ("issues", "NONE", "on", False),
            ("issues", "FIRST_TIMER", "on", False),
            ("issues", "FIRST_TIME_CONTRIBUTOR", "on", False),
            ("issues", "MANNEQUIN", "on", False),
            ("issues", None, "on", False),  # missing/null association -> fail-closed
            ("issues", "OWNER", "on", True),
            ("issues", "MEMBER", "on", True),
            ("issues", "COLLABORATOR", "on", True),
            ("issue_comment", "NONE", "on", False),
            ("issue_comment", "FIRST_TIMER", "on", False),
            ("issue_comment", "COLLABORATOR", "on", True),
        ],
    )
    def test_matrix(self, event_name, association, caller_auth, expected):
        assert trigger_starts(event_name, association, caller_auth) is expected

    def test_workflow_call_exempt_from_association_check(self):
        for association in ALLOWED_ASSOCIATIONS + DENIED_ASSOCIATIONS:
            assert trigger_starts("workflow_call", association, "on") is True

    def test_closed_action_never_starts(self):
        assert trigger_starts("issues", "OWNER", "on", action="closed") is False

    def test_bot_marker_still_skipped_even_for_authorized_author(self):
        assert trigger_starts("issues", "OWNER", "on", body=BOT_BODY) is False

    def test_machine_token_comment_still_triggers_for_authorized_author(self):
        assert trigger_starts("issue_comment", "COLLABORATOR", "on", body=MACHINE_BODY) is True

    def test_machine_token_comment_skipped_for_unauthorized_author(self):
        assert trigger_starts("issue_comment", "NONE", "on", body=MACHINE_BODY) is False


class TestCallerAuthDefault:
    """The gate is opt-out: the reusable workflow defaults `CALLER_AUTH` to `on`
    so a public repo is defensive before any explicit configuration (issue #831).
    """

    def test_workflow_defaults_caller_auth_on(self):
        workflow = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
        # PyYAML parses the unquoted `on:` top-level key as the boolean True.
        inputs = workflow[True]["workflow_call"]["inputs"]
        assert inputs["CALLER_AUTH"]["default"] == "on"
