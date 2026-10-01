"""Spec-scenario tests for ``adapters/github.py`` (issue #590, plan section 3.4).

The GitHub adapter composes the *existing* ``gh``/REST clients, so the client
surface is injected as a module-shaped stand-in in tests.
"""

from __future__ import annotations

import json

import pytest

from adapters.github import GitHubAdapter
from engine.models import Trigger

ISSUE_COMMENT_PAYLOAD = {
    "action": "created",
    "comment": {
        "id": 5927119915,
        "body": "/workflow run workflow --name spec-driven-impl",
        "created_at": "2026-10-01T07:47:47Z",
        "user": {"login": "olivier-norture"},
    },
    "issue": {"number": 590, "title": "[engine] Stage A", "user": {"login": "olivier-norture"}},
    "repository": {"full_name": "olivier-norture/git-flow-ai"},
}


class FakeGhClient:
    """Stand-in for the ``github_client`` module (function surface only)."""

    def __init__(self, comments=(), posted=None):
        self.comments = list(comments)
        self.posted = posted if posted is not None else {"id": 4242, "html_url": "https://example.test/c/4242"}
        self.calls = []

    def get_issue_comments(self, repo, issue_number, token=None, dry_run=False):
        self.calls.append(("get_issue_comments", repo, issue_number))
        return list(self.comments)

    def post_comment(self, repo, issue_number, body, token=None, dry_run=False):
        self.calls.append(("post_comment", repo, issue_number, body))
        return self.posted


def _event_file(tmp_path, payload):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload))
    return path


def _adapter(tmp_path, client):
    """GitHub adapter wired to the recorded issue-comment event."""
    return GitHubAdapter(
        repo="olivier-norture/git-flow-ai",
        event_path=_event_file(tmp_path, ISSUE_COMMENT_PAYLOAD),
        client=client,
    )


class TestReadTrigger:
    def test_normalizes_a_recorded_issue_comment_event(self, tmp_path):
        adapter = GitHubAdapter(
            repo="olivier-norture/git-flow-ai",
            event_path=_event_file(tmp_path, ISSUE_COMMENT_PAYLOAD),
            client=FakeGhClient(),
        )

        trigger = adapter.read_trigger()

        assert isinstance(trigger, Trigger)
        assert (trigger.author, trigger.body, trigger.type, trigger.number) == (
            "olivier-norture",
            "/workflow run workflow --name spec-driven-impl",
            "issue_comment",
            590,
        )

    def test_defaults_to_the_github_event_path_environment_variable(self, tmp_path, monkeypatch):
        path = _event_file(tmp_path, ISSUE_COMMENT_PAYLOAD)
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
        adapter = GitHubAdapter(repo="olivier-norture/git-flow-ai", client=FakeGhClient())

        assert adapter.read_trigger().number == 590


class TestHistory:
    def test_maps_client_comments_to_comment_entries(self, tmp_path):
        client = FakeGhClient(
            comments=[
                {
                    "body": "<!-- bot-workflow-terminal -->\nfirst",
                    "user": {"login": "olivier-norture"},
                    "created_at": "2026-10-01T07:47:47Z",
                }
            ]
        )
        adapter = _adapter(tmp_path, client)
        adapter.read_trigger()

        history = list(adapter.load_history())

        assert len(history) == 1
        assert history[0].author == "olivier-norture"
        assert history[0].body.endswith("first")
        assert history[0].created_at == "2026-10-01T07:47:47Z"

    def test_reads_the_issue_number_from_the_trigger(self, tmp_path):
        client = FakeGhClient()
        adapter = _adapter(tmp_path, client)

        adapter.read_trigger()
        list(adapter.load_history())

        assert ("get_issue_comments", "olivier-norture/git-flow-ai", 590) in client.calls


class TestPost:
    def test_returns_the_posted_comment_reference(self, tmp_path):
        client = FakeGhClient()
        adapter = _adapter(tmp_path, client)
        adapter.read_trigger()

        result = adapter.post("hello")

        assert "4242" in result
        assert ("post_comment", "olivier-norture/git-flow-ai", 590, "hello") in client.calls
