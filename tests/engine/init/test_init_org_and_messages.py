"""Org-scope checks, value loop, message and gh-argument contract
(``nebuko init``, issue #1272 rev 17).

The adapters are faked, so these tests pin the CLI orchestration contract:
the exit codes, the org checks, the per-secret messages and the exact ``gh``
arguments — without touching GitHub.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.init import cli
from nebuko.engine.init.ports import PreflightReport, SetupError, UsageError
from nebuko.engine.init.refs import StaticRefResolver
from nebuko.engine.init.setup import SetupContext
from nebuko.engine.init.sources import FileTemplateSource

REPO_ROOT = Path(__file__).resolve().parents[3]
PIN = "v1.2.3"
PR_URL = "https://github.com/acme/widgets/pull/7"
ORG_FORCE_STOP = "org secrets cannot be force-updated yet (see #1315)"


class FakeHost:
    """Read-only host double: preflight, App detection and secret listing."""

    def __init__(self, app_installed=True):
        self.app = app_installed

    def gh_available(self):
        return True

    def preflight(self, target):
        return PreflightReport(ok=True, missing_scopes=(), remediation="")

    def app_installed(self, target):
        return self.app


class GhInitClient:
    """A ``GitHubInitClient`` double recording the plan/execute traffic."""

    def __init__(self):
        self.committed: dict[str, str] | None = None
        self.branch: str | None = None
        self.secrets: dict[str, object] = {}
        self.existing_files: dict[str, bool] = {}
        self.existing: frozenset[str] = frozenset()
        self.pr_calls: list[dict] = []
        self.secret_args: list[list[str]] = []
        self.secret_stdin: list[str] = []

    def get_existing_files(self, target, paths):
        return {path: self.existing_files.get(path, False) for path in paths}

    def existing_secrets(self, target):
        return self.existing

    def upsert_branch_and_commit(self, target, branch, files):
        self.branch = branch
        self.committed = files

    def get_or_open_pull_request(self, target, branch, title, body):
        self.pr_calls.append({"branch": branch, "title": title, "body": body})
        return PR_URL

    def put_secret(self, target, name, value):
        self.secrets[name] = value


class FakePrompter:
    def __init__(self, texts=(), choices=(), secrets=(), confirm_answer=False):
        self.texts = list(texts)
        self.choices = list(choices)
        self.secret_values = list(secrets)
        self.confirm_answer = confirm_answer
        self.secret_prompts: list[str] = []

    def ask_text(self, label, default=""):
        return self.texts.pop(0) if self.texts else default

    def ask_choice(self, label, options, default=""):
        return self.choices.pop(0) if self.choices else default

    def ask_secret(self, name):
        self.secret_prompts.append(name)
        return self.secret_values.pop(0) if self.secret_values else ""

    def confirm(self, label, default=False):
        return self.confirm_answer


def _wire(monkeypatch, host, prompter=None, client=None):
    client = client or GhInitClient()
    context = SetupContext(
        source=FileTemplateSource(REPO_ROOT),
        refs=StaticRefResolver(PIN),
        host=host,
        client=client,
    )
    monkeypatch.setattr(cli, "build_setup", lambda **kwargs: context)
    if prompter is not None:
        monkeypatch.setattr(cli, "InitPrompter", lambda: prompter)
    return context


def _set_secrets(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(f"NEBUKO_SECRET_{name}", value)


# ── org checks in _run (D3) ──────────────────────────────────────────────────


# Given --force at org scope, when the CLI runs, then it exits 2 before any
# prompt or write, with a message naming #1315
def test_org_force_is_a_usage_error_before_any_prompt_or_write(monkeypatch, capsys):
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--org", "acme", "--non-interactive", "--force"])

    assert code == 2
    captured = capsys.readouterr()
    assert ORG_FORCE_STOP in captured.err
    assert client.pr_calls == [] and client.secrets == {}


# Given an org that does not match the repo owner (case-insensitively), when
# the CLI runs, then it exits 2 before any prompt or write
def test_org_not_matching_repo_owner_is_a_usage_error(monkeypatch, capsys):
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--org", "Acme", "--non-interactive"])

    assert code == 2
    assert "acme" in capsys.readouterr().err.lower()
    assert client.pr_calls == [] and client.secrets == {}


# Given an org matching the repo owner and no --force, when the CLI runs,
# then the secrets are created at org level and the PR targets the repo
def test_org_scope_create_targets_the_org_and_the_repo_pr(monkeypatch):
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--org", "acme", "--non-interactive"])

    assert code == 0
    assert client.branch == "nebuko/init"
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123", "GH_PAT": "pat-456"}


# ── value loop (D5) ──────────────────────────────────────────────────────────


# Given a skipped secret in non-interactive mode, when the CLI runs, then no
# value is requested for it and the run succeeds
def test_non_interactive_does_not_require_the_skipped_secret_env(monkeypatch, capsys):
    client = GhInitClient()
    client.existing = frozenset({"GH_PAT"})
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    assert code == 0
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123"}


# Given an empty interactive answer, when the value loop runs, then the secret
# ends as failed with "no value provided", exactly one prompt is made, and the
# run exits 1
def test_empty_interactive_answer_fails_with_one_prompt(monkeypatch, capsys):
    client = GhInitClient()
    prompter = FakePrompter(
        texts=["acme/widgets", "ubuntu-latest"],
        choices=["repository", "openrouter"],
        secrets=["", ""],
    )
    _wire(monkeypatch, FakeHost(), prompter, client=client)

    code = cli.main(["--pat"])

    captured = capsys.readouterr()
    assert code == 1
    assert len(prompter.secret_prompts) == 1
    assert "No value for OPENROUTER_API_KEY; skipping." in captured.out
    assert "no value provided" in captured.err
    assert client.secrets == {}


# ── messages (D6) ────────────────────────────────────────────────────────────


# Given a fresh repo with values for every secret, when the run succeeds, then
# one "Created: <name>" line is printed per secret
def test_created_messages_one_per_secret(monkeypatch, capsys):
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--pat", "--non-interactive"]) == 0

    out = capsys.readouterr().out
    assert "Created: OPENROUTER_API_KEY" in out
    assert "Created: GH_PAT" in out
    assert "Failed:" not in out


# Given a secret whose write fails, when the run finishes, then one
# "Failed: <name>: <detail>" line per failed secret is on stderr
def test_failed_message_carries_the_detail(monkeypatch, capsys):
    client = GhInitClient()

    def fail(target, name, value):
        raise SetupError("gh secret set failed: boom")

    client.put_secret = fail
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--pat", "--non-interactive"])

    captured = capsys.readouterr()
    assert code == 1
    assert "Failed: OPENROUTER_API_KEY: gh secret set failed: boom" in captured.err
    assert "Failed: GH_PAT: gh secret set failed: boom" in captured.err


# Given an existing secret at repo scope without --force, when the run
# finishes, then the skip note names --force
def test_repo_scope_skip_message_names_force(monkeypatch, capsys):
    client = GhInitClient()
    client.existing = frozenset({"OPENROUTER_API_KEY"})
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--non-interactive"]) == 0

    out = capsys.readouterr().out
    assert "Skipped: OPENROUTER_API_KEY: already exists; use --force to update" in out


# Given an existing secret at org scope without --force, when the run
# finishes, then the skip note names the org and the unchecked access
def test_org_scope_skip_message_names_the_org(monkeypatch, capsys):
    client = GhInitClient()
    client.existing = frozenset({"OPENROUTER_API_KEY"})
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--org", "acme", "--non-interactive"]) == 0

    out = capsys.readouterr().out
    assert (
        "Skipped: OPENROUTER_API_KEY: already exists in acme; "
        "access from this repo not checked (see #1315)" in out
    )


# ── exact gh arguments (D4) ─────────────────────────────────────────────────


# Given a repo-scope run, when the real client writes a secret, then the gh
# argv is exactly ["gh", "secret", "set", name, "--repo", owner/name] and the
# value travels on stdin — never --body
def test_put_secret_repo_scope_uses_exact_gh_args(monkeypatch):
    from nebuko.engine.init.github_init_client import GhInitClient as RealClient

    calls: list[tuple[list[str], str]] = []

    def runner(args, capture_output=True, text=True, input=None):
        calls.append((args, input or ""))
        from unittest.mock import Mock

        result = Mock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    real = RealClient(runner=runner)
    from nebuko.engine.init.core import SecretTarget

    real.put_secret(SecretTarget(scope="repo", repo="acme/widgets"), "OPENROUTER_API_KEY", "v")

    args, stdin = calls[0]
    assert args == ["gh", "secret", "set", "OPENROUTER_API_KEY", "--repo", "acme/widgets"]
    assert stdin == "v"
    assert "--body" not in " ".join(args)


# Given an org-scope create, when the real client writes a secret, then the
# gh argv carries --org, --visibility selected and --repos <owner/name>
def test_put_secret_org_create_uses_exact_gh_args(monkeypatch):
    from nebuko.engine.init.github_init_client import GhInitClient as RealClient
    from nebuko.engine.init.core import SecretTarget

    calls: list[tuple[list[str], str]] = []

    def runner(args, capture_output=True, text=True, input=None):
        calls.append((args, input or ""))
        from unittest.mock import Mock

        result = Mock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    real = RealClient(runner=runner)
    real.put_secret(
        SecretTarget(scope="org", repo="acme/widgets", org="acme"),
        "OPENROUTER_API_KEY",
        "v",
    )

    args, stdin = calls[0]
    assert args == [
        "gh", "secret", "set", "OPENROUTER_API_KEY",
        "--org", "acme", "--visibility", "selected", "--repos", "acme/widgets",
    ]
    assert stdin == "v"
    assert "--body" not in " ".join(args)


# ── run_init-level behaviour (N12/N13/N15/N16/N37) ──────────────────────────


# Given any run, when run_init completes, then no file is written to the local
# checkout (the rendered files live only on the nebuko/init branch)
def test_run_init_writes_no_local_files(monkeypatch, tmp_path):
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["--repo", "acme/widgets", "--non-interactive"]) == 0

    assert list(tmp_path.rglob("nebuko.yaml")) == []
    assert not (tmp_path / ".nebuko").exists()


# Given a host whose secret listing raises, when run_init runs, then the
# exception propagates (no silent empty set, no accidental "create" plan)
def test_run_init_propagates_existing_secrets_failure():
    class RaisingHost:
        def get_existing_files(self, target, paths):
            return {}

        def existing_secrets(self, target):
            raise RuntimeError("listing failed")

    with pytest.raises(RuntimeError):
        from nebuko.engine.init import setup

        setup.run_init(
            _answers(),
            source=FileTemplateSource(REPO_ROOT),
            host=RaisingHost(),
            prompter=FakePrompter(),
            pat_mode=False,
            non_interactive=True,
        )


# Given a closed/merged PR for the branch, when run_init runs, then the PR is
# not reused (get_or_open_pull_request sees only the OPEN PR)
def test_closed_pull_request_is_not_reused(monkeypatch):
    client = GhInitClient()
    client.pr_state = "closed"
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--non-interactive"]) == 0

    # the fake opened a new PR for this run
    assert client.pr_calls[-1]["branch"] == "nebuko/init"


def _answers():
    from nebuko.engine.init.ports import InitAnswers, ProviderProfile, SetupTarget

    class _Profile(ProviderProfile):
        name = "openrouter"
        description = "OpenRouter (default)"
        template_path = "defaults/agents/agent-config-openrouter.yaml"
        secret_names = ("OPENROUTER_API_KEY",)

    return InitAnswers(
        target=SetupTarget(host="github.com", owner="acme", repo="widgets"),
        provider=_Profile(),
        runs_on="ubuntu-latest",
        pin_ref=PIN,
    )
