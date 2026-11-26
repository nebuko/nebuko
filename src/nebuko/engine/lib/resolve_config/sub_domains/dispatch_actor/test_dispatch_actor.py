"""Verified dispatch actor for personal-repo gate decisions (issue #1482)."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from nebuko.engine.lib.resolve_config.sub_domains.dispatch_actor import dispatch_actor as da
from nebuko.engine.lib.resolve_config.sub_domains.dispatch_actor.dispatch_actor import (
    lookup_collaborator_association,
    personal_dispatch_actor,
    verify_dispatch_actor,
)

PERSONAL = "alice/nebuko-personal"
PROJECT = "acme/app"
TOKEN = "ghp_SECRET_TOKEN_VALUE"


@pytest.fixture
def dispatch_env(tmp_path, monkeypatch):
    """Runner-set values of an owner's dispatch into the personal repo."""
    for key in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_REPOSITORY",
                "GITHUB_REPOSITORY_OWNER", "REPO_NAME", "GH_TOKEN", "CALLER_AUTH"):
        monkeypatch.delenv(key, raising=False)

    def make(actor="alice", repository=PERSONAL, owner="alice", repo_name=PROJECT, token=TOKEN):
        event = tmp_path / "event.json"
        event.write_text(json.dumps({"client_payload": {"actor": actor}}))
        env = {
            "GITHUB_EVENT_NAME": "repository_dispatch",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_REPOSITORY": repository,
            "GITHUB_REPOSITORY_OWNER": owner,
            "REPO_NAME": repo_name,
            "GH_TOKEN": token,
            "CALLER_AUTH": "COLLABORATOR",
        }
        return env

    return make


def _fake_gh(monkeypatch, *, stdout="", stderr="", returncode=0, calls=None, exc=None):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        if exc is not None:
            raise exc
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr(da.subprocess, "run", run)


class TestPersonalDispatchActor:
    def test_owner_dispatch_on_personal_repo_is_verified(self, dispatch_env):
        assert personal_dispatch_actor(dispatch_env()) == ("alice", "")

    def test_owner_match_is_case_insensitive(self, dispatch_env):
        assert personal_dispatch_actor(dispatch_env(actor="Alice"))[1] == ""

    def test_non_dispatch_event_is_not_a_dispatch(self, dispatch_env):
        env = dispatch_env()
        env["GITHUB_EVENT_NAME"] = "issue_comment"
        assert personal_dispatch_actor(env) == ("", "not-a-dispatch")

    def test_project_repo_is_not_the_personal_repo(self, dispatch_env):
        env = dispatch_env(repository=PROJECT)
        assert personal_dispatch_actor(env) == ("alice", "not-personal-repo")

    def test_actor_other_than_owner_is_refused(self, dispatch_env):
        assert personal_dispatch_actor(dispatch_env(actor="bob")) == ("bob", "not-owner")

    def test_unknown_owner_is_refused(self, dispatch_env):
        assert personal_dispatch_actor(dispatch_env(owner=""))[1] == "not-owner"


class TestLookupCollaboratorAssociation:
    @pytest.mark.parametrize("permission", ["admin", "maintain", "write"])
    def test_write_permissions_map_to_collaborator(self, monkeypatch, permission):
        _fake_gh(monkeypatch, stdout=f"{permission}\n")
        assert lookup_collaborator_association(PROJECT, "alice", TOKEN) == ("COLLABORATOR", "")

    @pytest.mark.parametrize("permission", ["read", "triage", "none"])
    def test_below_write_is_no_write_access(self, monkeypatch, permission):
        _fake_gh(monkeypatch, stdout=f"{permission}\n")
        assert lookup_collaborator_association(PROJECT, "alice", TOKEN) == (None, "no-write-access")

    def test_403_is_refused_by_github_not_no_write(self, monkeypatch):
        _fake_gh(monkeypatch, returncode=1, stderr="gh: Must have admin rights (HTTP 403)")
        assert lookup_collaborator_association(PROJECT, "alice", TOKEN) == (
            None, "lookup-refused-by-github",
        )

    def test_404_is_refused_by_github_not_no_write(self, monkeypatch):
        _fake_gh(monkeypatch, returncode=1, stderr="gh: Not Found (HTTP 404)")
        assert lookup_collaborator_association(PROJECT, "alice", TOKEN) == (
            None, "lookup-refused-by-github",
        )

    def test_network_failure_is_unavailable(self, monkeypatch):
        _fake_gh(monkeypatch, returncode=1, stderr="error connecting to api.github.com")
        assert lookup_collaborator_association(PROJECT, "alice", TOKEN) == (
            None, "lookup-unavailable",
        )

    def test_timeout_is_reported_as_timed_out(self, monkeypatch):
        _fake_gh(monkeypatch, exc=subprocess.TimeoutExpired(cmd="gh", timeout=30))
        assert lookup_collaborator_association(PROJECT, "alice", TOKEN) == (
            None, "lookup-timed-out",
        )

    def test_missing_gh_binary_is_unavailable(self, monkeypatch):
        _fake_gh(monkeypatch, exc=OSError("no gh"))
        assert lookup_collaborator_association(PROJECT, "alice", TOKEN) == (
            None, "lookup-unavailable",
        )

    def test_empty_token_is_refused_without_calling_gh(self, monkeypatch):
        calls: list = []
        _fake_gh(monkeypatch, stdout="admin\n", calls=calls)
        assert lookup_collaborator_association(PROJECT, "alice", "  ") == (
            None, "no-project-token",
        )
        assert calls == []

    def test_token_travels_in_env_not_argv(self, monkeypatch):
        calls: list = []
        _fake_gh(monkeypatch, stdout="write\n", calls=calls)
        lookup_collaborator_association(PROJECT, "alice", TOKEN)
        argv, kwargs = calls[0]
        assert TOKEN not in " ".join(argv)
        assert kwargs["env"]["GH_TOKEN"] == TOKEN
        assert "PATH" in kwargs["env"]
        assert kwargs["timeout"] == 30.0
        assert "repos/acme/app/collaborators/alice/permission" in argv[2]


class TestVerifyDispatchActor:
    def test_owner_with_write_on_project_repo_is_verified(self, dispatch_env, monkeypatch):
        calls: list = []
        _fake_gh(monkeypatch, stdout="write\n", calls=calls)
        check = verify_dispatch_actor(dispatch_env())
        assert check.verified is True
        assert (check.actor, check.association, check.reason) == ("alice", "COLLABORATOR", "")
        assert "repos/acme/app/collaborators/alice/permission" in calls[0][0][2]

    def test_caller_flags_do_not_make_a_non_owner_verified(self, dispatch_env, monkeypatch):
        calls: list = []
        _fake_gh(monkeypatch, stdout="admin\n", calls=calls)
        check = verify_dispatch_actor(dispatch_env(actor="bob"))
        assert check.verified is False
        assert check.reason == "not-owner"
        assert calls == []

    def test_project_repo_dispatch_never_looks_up(self, dispatch_env, monkeypatch):
        calls: list = []
        _fake_gh(monkeypatch, stdout="admin\n", calls=calls)
        check = verify_dispatch_actor(dispatch_env(repository=PROJECT))
        assert check.verified is False
        assert check.reason == "not-personal-repo"
        assert calls == []

    def test_missing_project_repo_never_falls_back_to_personal_repo(self, dispatch_env, monkeypatch):
        calls: list = []
        _fake_gh(monkeypatch, stdout="admin\n", calls=calls)
        check = verify_dispatch_actor(dispatch_env(repo_name=""))
        assert (check.verified, check.reason) == (False, "no-project-repo")
        assert calls == []
