from __future__ import annotations

from nebuko.engine.lib.repo_resolution import resolve_repo
from nebuko.engine.models import Trigger


def _dispatch(payload=None, org_repo=""):
    return Trigger(
        author="alice",
        body="",
        type="repository_dispatch",
        number=1388,
        payload=payload or {},
        org_repo=org_repo,
    )


class TestResolveRepo:
    def test_the_exported_env_wins_over_a_dispatch_caller_payload(self):
        trigger = _dispatch({"repository": {"full_name": "olivier-norture/nebuko-claude-code"}})

        env = {"REPO_NAME": "nebuko/nebuko", "GITHUB_REPOSITORY": "nebuko/nebuko"}

        assert resolve_repo(env, trigger) == "nebuko/nebuko"

    def test_github_repository_is_used_when_repo_name_is_unset(self):
        assert resolve_repo({"GITHUB_REPOSITORY": "acme/app"}, _dispatch()) == "acme/app"

    def test_trigger_org_repo_precedes_the_payload(self):
        trigger = _dispatch({"repository": {"full_name": "caller/repo"}}, org_repo="acme/app")

        assert resolve_repo({}, trigger) == "acme/app"

    def test_client_payload_org_repo_is_a_fallback(self):
        trigger = _dispatch({"client_payload": {"org_repo": "acme/app"}})

        assert resolve_repo({}, trigger) == "acme/app"

    def test_an_issue_trigger_keeps_the_payload_repository(self):
        trigger = Trigger(
            author="alice",
            body="",
            type="issue_comment",
            number=7,
            payload={"repository": {"full_name": "o/r"}},
        )

        assert resolve_repo({}, trigger) == "o/r"

    def test_no_source_resolves_to_empty(self):
        assert resolve_repo({}, None) == ""
        assert resolve_repo({}, _dispatch()) == ""
