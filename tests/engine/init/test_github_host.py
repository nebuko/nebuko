"""``gh`` adapter contract for ``nebuko init`` (issue #991).

The fake runner records every argv and stdin, so these tests pin the two
security-relevant properties: the secret value only ever travels on stdin, and
the preflight explains a missing ``workflow`` scope instead of failing midway.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from nebuko.engine.init import github_host
from nebuko.engine.init.github_host import GitHubHost
from nebuko.engine.init.ports import GeneratedFiles, SetupError, SetupTarget

TARGET = SetupTarget(host="github.com", owner="acme", repo="widgets")
ORG_TARGET = SetupTarget(host="github.com", owner="acme", repo="widgets", org="acme")

FILES = GeneratedFiles(
    agent_config_path=".nebuko/agents/agent-config.yaml",
    agent_config="providers: {}\n",
    workflow_path=".github/workflows/nebuko.yaml",
    workflow="name: Nebuko\n",
)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRunner:
    """Answers by substring match, most specific rule first."""

    def __init__(self, rules):
        self.rules = list(rules)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        joined = " ".join(args)
        for needle, result in self.rules:
            if needle in joined:
                return result
        return _completed(returncode=1, stderr=f"unexpected call: {joined}")

    def argv(self, needle: str) -> list[str]:
        for args, _ in self.calls:
            if needle in " ".join(args):
                return args
        raise AssertionError(f"no call matching {needle!r}")

    def stdin(self, needle: str) -> str:
        for args, kwargs in self.calls:
            if needle in " ".join(args):
                return kwargs.get("input") or ""
        raise AssertionError(f"no call matching {needle!r}")


def _auth_status(scopes: str | None) -> str:
    account = {"state": "success", "active": True, "host": "github.com", "login": "acme"}
    if scopes is not None:
        account["scopes"] = scopes
    return json.dumps({"hosts": {"github.com": [account]}})


# ── preflight ────────────────────────────────────────────────────────────────


def test_gh_available_reflects_the_path_lookup(monkeypatch):
    host = GitHubHost(runner=FakeRunner([]))
    monkeypatch.setattr(github_host.shutil, "which", lambda name: "/usr/bin/gh")
    assert host.gh_available() is True
    monkeypatch.setattr(github_host.shutil, "which", lambda name: None)
    assert host.gh_available() is False


def test_preflight_ok_when_the_workflow_scope_is_present():
    runner = FakeRunner([("auth status", _completed(_auth_status("repo, workflow")))])
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert report.ok
    assert report.missing_scopes == ()


def test_preflight_reports_the_missing_workflow_scope():
    runner = FakeRunner([("auth status", _completed(_auth_status("repo, read:org")))])
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert not report.ok
    assert report.missing_scopes == ("workflow",)
    assert report.remediation == "gh auth refresh -s workflow"


def test_preflight_falls_back_to_the_oauth_scopes_header():
    runner = FakeRunner(
        [
            ("auth status", _completed(_auth_status(None))),
            ("api -i user", _completed("HTTP/2.0 200 OK\nX-OAuth-Scopes: repo, workflow\n\n{}")),
        ]
    )
    assert GitHubHost(runner=runner).preflight(TARGET).ok


def test_preflight_does_not_block_when_scopes_are_unreported():
    runner = FakeRunner(
        [
            ("auth status", _completed(_auth_status(None))),
            ("api -i user", _completed("HTTP/2.0 200 OK\n\n{}")),
        ]
    )
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert report.ok
    assert report.missing_scopes == ()


def test_preflight_reports_a_missing_login():
    runner = FakeRunner([("auth status", _completed(returncode=1, stderr="not logged in"))])
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert not report.ok
    assert report.remediation == "gh auth login"


def test_preflight_reports_a_missing_login_when_no_host_is_active():
    # `gh auth status --json hosts` exits 0 with an empty host map when no token
    # is present, so an empty payload must not be read as "scopes unreported".
    runner = FakeRunner([("auth status", _completed(json.dumps({"hosts": {}})))])
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert not report.ok
    assert report.missing_scopes == ()
    assert report.remediation == "gh auth login"


def test_preflight_falls_back_to_the_api_when_the_status_json_is_unsupported():
    # gh 2.77-2.79 print the modern `auth status` output but reject the `hosts`
    # JSON field, so the status probe fails for a logged-in client.
    runner = FakeRunner(
        [
            ("auth status", _completed(returncode=1, stderr='Unknown JSON field: "hosts"')),
            ("api -i user", _completed("HTTP/2.0 200 OK\nX-OAuth-Scopes: repo, workflow\n\n{}")),
        ]
    )
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert report.ok
    assert report.missing_scopes == ()


def test_preflight_reports_the_missing_workflow_scope_from_the_api_fallback():
    runner = FakeRunner(
        [
            ("auth status", _completed(returncode=1, stderr='Unknown JSON field: "hosts"')),
            (
                "api -i user",
                _completed("HTTP/2.0 200 OK\nX-OAuth-Scopes: gist, read:org, repo\n\n{}"),
            ),
        ]
    )
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert not report.ok
    assert report.missing_scopes == ("workflow",)
    assert report.remediation == "gh auth refresh -s workflow"


def test_preflight_reports_a_missing_login_when_the_api_fallback_also_fails():
    runner = FakeRunner(
        [
            ("auth status", _completed(returncode=1, stderr='Unknown JSON field: "hosts"')),
            ("api -i user", _completed(returncode=1, stderr="gh: authentication required")),
        ]
    )
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert not report.ok
    assert report.missing_scopes == ()
    assert report.remediation == "gh auth login"


def test_preflight_does_not_block_when_the_api_fallback_reports_no_scopes():
    runner = FakeRunner(
        [
            ("auth status", _completed(returncode=1, stderr='Unknown JSON field: "hosts"')),
            ("api -i user", _completed("HTTP/2.0 200 OK\n\n{}")),
        ]
    )
    report = GitHubHost(runner=runner).preflight(TARGET)
    assert report.ok
    assert report.missing_scopes == ()


# ── secrets ──────────────────────────────────────────────────────────────────


def test_set_secret_pipes_the_value_on_stdin_never_in_argv():
    runner = FakeRunner([("secret set", _completed())])
    GitHubHost(runner=runner).set_secret(TARGET, "OPENROUTER_API_KEY", "s3cr3t-value")
    args = runner.argv("secret set")
    assert args == ["gh", "secret", "set", "OPENROUTER_API_KEY", "--repo", "acme/widgets"]
    assert "--body" not in args
    assert not any("s3cr3t-value" in arg for arg in args)
    assert runner.stdin("secret set") == "s3cr3t-value"


def test_set_secret_targets_the_org_when_asked():
    runner = FakeRunner([("secret set", _completed())])
    GitHubHost(runner=runner).set_secret(ORG_TARGET, "GH_PAT", "pat")
    assert runner.argv("secret set") == ["gh", "secret", "set", "GH_PAT", "--org", "acme"]


def test_existing_secrets_lists_the_repo_secret_names():
    runner = FakeRunner([("secret list", _completed("GH_PAT\nOPENROUTER_API_KEY\n"))])
    found = GitHubHost(runner=runner).existing_secrets(TARGET)
    assert found == frozenset({"GH_PAT", "OPENROUTER_API_KEY"})
    assert runner.argv("secret list") == [
        "gh",
        "secret",
        "list",
        "--repo",
        "acme/widgets",
        "--json",
        "name",
        "--jq",
        ".[].name",
    ]


def test_existing_secrets_targets_the_org_when_asked():
    runner = FakeRunner([("secret list", _completed(""))])
    found = GitHubHost(runner=runner).existing_secrets(ORG_TARGET)
    assert found == frozenset()
    assert runner.argv("secret list") == [
        "gh",
        "secret",
        "list",
        "--org",
        "acme",
        "--json",
        "name",
        "--jq",
        ".[].name",
    ]


def test_existing_secrets_raises_when_the_listing_fails():
    runner = FakeRunner([("secret list", _completed(returncode=1, stderr="HTTP 403"))])
    with pytest.raises(SetupError):
        GitHubHost(runner=runner).existing_secrets(TARGET)


# ── repository state ─────────────────────────────────────────────────────────


def test_existing_paths_keeps_only_the_paths_the_api_returns():
    runner = FakeRunner(
        [
            ("contents/.nebuko/agents/agent-config.yaml", _completed(".nebuko/agents/agent-config.yaml\n")),
            ("contents/.github/workflows/nebuko.yaml", _completed(returncode=1, stderr="Not Found")),
        ]
    )
    found = GitHubHost(runner=runner).existing_paths(
        TARGET, (".nebuko/agents/agent-config.yaml", ".github/workflows/nebuko.yaml")
    )
    assert found == {".nebuko/agents/agent-config.yaml"}


def test_app_installed_is_false_until_the_app_ships():
    assert GitHubHost(runner=FakeRunner([])).app_installed(TARGET) is False


# ── pull request ─────────────────────────────────────────────────────────────


def _pr_runner(pr_create=None):
    return FakeRunner(
        [
            ("git/ref/heads/main", _completed("headsha\n")),
            ("git/commits/headsha", _completed("basetree\n")),
            ("git/blobs", _completed("blobsha\n")),
            ("git/trees", _completed("treesha\n")),
            ("git/commits", _completed("commitsha\n")),
            ("git/ref/heads/nebuko/init", _completed(returncode=1, stderr="Not Found")),
            ("git/refs", _completed("refsha\n")),
            ("pr create", pr_create or _completed("https://github.com/acme/widgets/pull/7\n")),
            ("repos/acme/widgets --jq .default_branch", _completed("main\n")),
        ]
    )


def test_open_pr_commits_both_files_and_returns_the_url():
    runner = _pr_runner()
    url = GitHubHost(runner=runner).open_pr(TARGET, "nebuko/init", "title", "body", FILES)
    assert url == "https://github.com/acme/widgets/pull/7"

    blob_payloads = [
        json.loads(kwargs["input"])
        for args, kwargs in runner.calls
        if "git/blobs" in " ".join(args)
    ]
    assert [payload["content"] for payload in blob_payloads] == [
        FILES.agent_config,
        FILES.workflow,
    ]

    tree = json.loads(runner.stdin("git/trees"))
    assert tree["base_tree"] == "basetree"
    assert [entry["path"] for entry in tree["tree"]] == [
        FILES.agent_config_path,
        FILES.workflow_path,
    ]

    commit = json.loads(runner.stdin("--method POST repos/acme/widgets/git/commits"))
    assert commit["parents"] == ["headsha"]

    create = runner.argv("pr create")
    assert create[create.index("--head") + 1] == "nebuko/init"
    assert create[create.index("--base") + 1] == "main"


def test_open_pr_reuses_the_existing_branch_and_pull_request():
    runner = FakeRunner(
        [
            ("git/ref/heads/main", _completed("headsha\n")),
            ("git/commits/headsha", _completed("basetree\n")),
            ("git/blobs", _completed("blobsha\n")),
            ("git/trees", _completed("treesha\n")),
            ("git/commits", _completed("commitsha\n")),
            ("git/ref/heads/nebuko/init", _completed("refsha\n")),
            ("git/refs/heads/nebuko/init", _completed("refsha\n")),
            ("pr create", _completed(returncode=1, stderr="a pull request already exists")),
            ("pr view", _completed("https://github.com/acme/widgets/pull/7\n")),
            ("repos/acme/widgets --jq .default_branch", _completed("main\n")),
        ]
    )
    url = GitHubHost(runner=runner).open_pr(TARGET, "nebuko/init", "title", "body", FILES)
    assert url == "https://github.com/acme/widgets/pull/7"
    patch = runner.argv("git/refs/heads/nebuko/init")
    assert "--method" in patch and "PATCH" in patch
    assert json.loads(runner.stdin("git/refs/heads/nebuko/init"))["force"] is True
