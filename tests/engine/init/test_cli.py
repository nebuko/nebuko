"""CLI contract for ``nebuko init`` (issue #991).

The adapters are faked, so these tests pin the command surface: the 0/1/2 exit
codes, the non-interactive environment mode, the no-overwrite rule, and the
property that no secret value ever reaches the output or the pull request.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from nebuko.engine.init import cli
from nebuko.engine.init.core import AGENT_CONFIG_PATH
from nebuko.engine.init.ports import PreflightReport, SetupError, SetupHost
from nebuko.engine.init.refs import StaticRefResolver
from nebuko.engine.init.setup import SetupContext
from nebuko.engine.init.sources import FileTemplateSource
from nebuko.engine.run_loop import main as run_loop_main

REPO_ROOT = Path(__file__).resolve().parents[3]
PIN = "v1.2.3"
PR_URL = "https://github.com/acme/widgets/pull/7"


class FakeHost:
    """Read-only host double: preflight, App detection and gh availability."""

    def __init__(
        self,
        report=None,
        existing=(),
        app_installed=False,
        gh_available=True,
        existing_secrets=(),
    ):
        self.report = report or PreflightReport(ok=True, missing_scopes=(), remediation="")
        self.existing = set(existing)
        self.app = app_installed
        self.gh = gh_available
        self.present_secrets = frozenset(existing_secrets)
        self.secret_probes: list = []

    def gh_available(self):
        return self.gh

    def preflight(self, target):
        return self.report

    def app_installed(self, target):
        return self.app

    def existing_secrets(self, target):
        self.secret_probes.append(target)
        return self.present_secrets


class GhInitClient:
    """A ``GitHubInitClient`` double for the CLI orchestration tests.

    The production ``gh``-backed adapter lives in ``github_init_client``;
    this fake records the plan/execute traffic so the CLI contract (exit
    codes, prompts, no local writes) is pinned without touching GitHub.
    """

    def __init__(self):
        self.committed: dict[str, str] | None = None
        self.branch: str | None = None
        self.secrets: dict[str, object] = {}
        self.existing_files: dict[str, bool] = {}
        self.existing: frozenset[str] = frozenset()
        self.raise_on_list = False
        self.pr_calls: list[dict] = []

    def get_existing_files(self, target, paths):
        return {path: self.existing_files.get(path, False) for path in paths}

    def existing_secrets(self, target):
        if self.raise_on_list:
            raise SetupError("gh secret list failed: boom")
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
    def __init__(self, texts=(), choice="", choices=(), secrets=(), confirm_answer=False):
        self.texts = list(texts)
        self.choice = choice
        self.choices = list(choices)
        self.secret_values = list(secrets)
        self.confirm_answer = confirm_answer
        self.confirmations: list[str] = []

    def ask_text(self, label, default=""):
        return self.texts.pop(0) if self.texts else default

    def ask_choice(self, label, options, default=""):
        if self.choices:
            return self.choices.pop(0)
        return self.choice or default

    def ask_secret(self, name):
        return self.secret_values.pop(0) if self.secret_values else ""

    def confirm(self, label, default=False):
        self.confirmations.append(label)
        return self.confirm_answer


def _wire(monkeypatch, host, prompter=None, client=None):
    client = client or GhInitClient()
    context = SetupContext(
        source=FileTemplateSource(REPO_ROOT), refs=StaticRefResolver(PIN), host=host, client=client
    )
    monkeypatch.setattr(cli, "build_setup", lambda **kwargs: context)
    if prompter is not None:
        monkeypatch.setattr(cli, "InitPrompter", lambda: prompter)
    return context


def _wire_pat(monkeypatch, host, prompter=None, client=None):
    """Same wiring, forced into PAT mode (pre-App-default contract)."""
    _wire(monkeypatch, host, prompter, client=client)
    original = cli.main

    def main(argv=None):
        argv = list(argv) if argv is not None else None
        return original(["--pat", *(argv or [])])

    monkeypatch.setattr(cli, "main", main)
    return cli.main


def _set_secrets(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(f"NEBUKO_SECRET_{name}", value)


# ── non-interactive mode ─────────────────────────────────────────────────────


def test_non_interactive_opens_a_pr_and_sets_the_secrets(monkeypatch, capsys):
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--provider", "openrouter", "--pat", "--non-interactive"])

    captured = capsys.readouterr()
    assert code == 0
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123", "GH_PAT": "pat-456"}
    assert client.pr_calls[0]["branch"] == "nebuko/init"
    assert PR_URL in captured.out
    assert "key-123" not in captured.out + captured.err
    assert "pat-456" not in captured.out + captured.err


def test_the_pull_request_carries_the_config_and_the_pinned_workflow(monkeypatch):
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--pat", "--non-interactive"]) == 0

    files = client.committed
    assert AGENT_CONFIG_PATH in files
    assert ".github/workflows/nebuko.yaml" in files
    assert "key-123" not in files[".github/workflows/nebuko.yaml"] + client.pr_calls[0]["body"]
    assert f"nebuko-workflow.yaml@{PIN}" in files[".github/workflows/nebuko.yaml"]


def test_no_file_is_written_to_the_local_checkout(monkeypatch, tmp_path, capsys):
    """The new flow never writes the rendered files to the local checkout."""
    monkeypatch.chdir(tmp_path)
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--pat", "--non-interactive"]) == 0

    assert list(tmp_path.rglob("nebuko.yaml")) == []
    assert not (tmp_path / ".nebuko").exists()


def test_missing_workflow_scope_exits_two_with_the_remediation(monkeypatch, capsys):
    client = GhInitClient()
    _wire(
        monkeypatch,
        FakeHost(
            report=PreflightReport(
                ok=False, missing_scopes=("workflow",), remediation="gh auth refresh -s workflow"
            )
        ),
        client=client,
    )
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--pat", "--non-interactive"])

    assert code == 2
    assert "gh auth refresh -s workflow" in capsys.readouterr().err
    assert client.pr_calls == []
    assert client.secrets == {}


def test_missing_workflow_scope_is_checked_before_the_secret_prompts(monkeypatch, capsys):
    client = GhInitClient()
    prompter = FakePrompter(texts=["acme/widgets"], choice="openrouter", secrets=["key", "pat"])
    _wire(
        monkeypatch,
        FakeHost(
            report=PreflightReport(
                ok=False, missing_scopes=("workflow",), remediation="gh auth refresh -s workflow"
            )
        ),
        prompter,
        client=client,
    )

    code = cli.main([])

    assert code == 2
    assert prompter.secret_values == ["key", "pat"]  # the scope check runs before any secret prompt
    assert "gh auth refresh -s workflow" in capsys.readouterr().err
    assert client.pr_calls == []


def test_success_prints_the_next_steps(monkeypatch, capsys):
    _wire(monkeypatch, FakeHost())
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--pat", "--non-interactive"]) == 0

    out = capsys.readouterr().out
    print("CAPTURED", repr(out))
    assert "Next steps" in out
    assert f"1. Review and merge the pull request: {PR_URL}" in out
    assert 'comment "@@dev Hello Nebuko!" on an issue in acme/widgets.' in out
    assert "Let's build something with Nebuko's agent teams" in out


def test_the_wordmark_is_printed_first(monkeypatch, capsys):
    _wire(monkeypatch, FakeHost())
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--pat", "--non-interactive"]) == 0

    out = capsys.readouterr().out
    print("CAPTURED", repr(out))
    assert out.startswith(cli.WORDMARK)
    assert "███╗   ██╗" in out


def test_the_wordmark_is_printed_before_the_gh_check(monkeypatch, capsys):
    _wire(monkeypatch, FakeHost(gh_available=False))

    assert cli.main(["--repo", "acme/widgets", "--non-interactive"]) == 2
    assert capsys.readouterr().out.startswith(cli.WORDMARK)


# ── gh availability ──────────────────────────────────────────────────────────


def test_missing_gh_cli_exits_two_with_an_install_hint(monkeypatch, capsys):
    client = GhInitClient()
    _wire_pat(monkeypatch, FakeHost(gh_available=False), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    err = capsys.readouterr().err
    assert code == 2
    assert "GitHub CLI (gh)" in err
    assert "cli.github.com" in err
    assert client.pr_calls == []
    assert client.secrets == {}


def test_missing_gh_cli_fails_before_asking_anything(monkeypatch, capsys):
    prompter = FakePrompter(texts=["acme/widgets"], choice="openrouter", secrets=["key", "pat"])
    _wire(monkeypatch, FakeHost(gh_available=False), prompter)

    assert cli.main([]) == 2
    assert prompter.texts == ["acme/widgets"]  # the check runs before any prompt
    assert "GitHub CLI (gh)" in capsys.readouterr().err


def test_non_interactive_requires_the_secret_env_vars(monkeypatch, capsys):
    client = GhInitClient()
    _wire(monkeypatch, FakeHost(), client=client)
    monkeypatch.delenv("NEBUKO_SECRET_OPENROUTER_API_KEY", raising=False)

    code = cli.main(["--repo", "acme/widgets", "--pat", "--non-interactive"])

    assert code == 2
    assert "NEBUKO_SECRET_OPENROUTER_API_KEY" in capsys.readouterr().err
    assert client.pr_calls == []


def test_non_interactive_requires_the_repo_flag(monkeypatch, capsys):
    _wire(monkeypatch, FakeHost())
    assert cli.main(["--non-interactive"]) == 2
    assert "--repo is required" in capsys.readouterr().err


def test_org_flag_targets_org_level_secrets(monkeypatch):
    client = GhInitClient()
    _wire_pat(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--org", "acme", "--non-interactive"])

    assert code == 0
    assert client.branch == "nebuko/init"
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123", "GH_PAT": "pat-456"}


def test_non_interactive_defaults_to_repository_level_secrets(monkeypatch):
    client = GhInitClient()
    _wire_pat(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--non-interactive"]) == 0
    assert client.branch == "nebuko/init"


def test_interactive_asks_where_to_store_the_secrets(monkeypatch, capsys):
    client = GhInitClient()
    prompter = FakePrompter(
        texts=["acme/widgets", "acme", "ubuntu-latest"],
        choices=["organization", "openrouter"],
        secrets=["key-123", "pat-456"],
    )
    _wire_pat(monkeypatch, FakeHost(), prompter, client=client)

    assert cli.main([]) == 0
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123", "GH_PAT": "pat-456"}


def test_interactive_defaults_to_repository_level_secrets(monkeypatch):
    client = GhInitClient()
    prompter = FakePrompter(
        texts=["acme/widgets", "ubuntu-latest"],
        choices=["repository", "openrouter"],
        secrets=["key-123", "pat-456"],
    )
    _wire_pat(monkeypatch, FakeHost(), prompter, client=client)

    assert cli.main([]) == 0
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123", "GH_PAT": "pat-456"}


def test_unknown_provider_is_a_usage_error(monkeypatch):
    _wire(monkeypatch, FakeHost())
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--repo", "acme/widgets", "--provider", "nope", "--non-interactive"])
    assert excinfo.value.code == 2


# ── idempotence ──────────────────────────────────────────────────────────────


def test_existing_config_is_not_overwritten_without_confirmation(monkeypatch, capsys):
    client = GhInitClient()
    client.existing_files = {".nebuko/agents/agent-config.yaml": True}
    _wire_pat(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    assert code == 1
    assert client.pr_calls == []
    assert "left untouched" in capsys.readouterr().err


def test_force_overwrites_existing_config(monkeypatch):
    client = GhInitClient()
    client.existing_files = {".github/workflows/nebuko.yaml": True}
    _wire_pat(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive", "--force"])

    assert code == 0
    assert len(client.pr_calls) == 1


def test_interactive_run_asks_before_overwriting(monkeypatch, capsys):
    client = GhInitClient()
    client.existing_files = {".nebuko/agents/agent-config.yaml": True}
    prompter = FakePrompter(
        texts=["acme/widgets", "ubuntu-latest"],
        choices=["repository", "openrouter"],
        secrets=["key-123", "pat-456"],
        confirm_answer=True,
    )
    _wire_pat(monkeypatch, FakeHost(), prompter, client=client)

    assert cli.main([]) == 0
    assert prompter.confirmations
    assert len(client.pr_calls) == 1


def test_conflicts_aborts_interactively_when_declined(monkeypatch, capsys):
    client = GhInitClient()
    client.existing_files = {".github/workflows/nebuko.yaml": True}
    prompter = FakePrompter(
        texts=["acme/widgets", "ubuntu-latest"],
        choices=["repository", "openrouter"],
        secrets=["key-123", "pat-456"],
        confirm_answer=False,
    )
    _wire_pat(monkeypatch, FakeHost(), prompter, client=client)

    assert cli.main([]) == 1
    assert client.pr_calls == []
    assert "left untouched" in capsys.readouterr().err


# ── secrets already stored ───────────────────────────────────────────────────


def test_existing_org_secrets_are_skipped_and_reported(monkeypatch, capsys):
    client = GhInitClient()
    client.existing = frozenset({"GH_PAT", "OPENROUTER_API_KEY"})
    _wire_pat(monkeypatch, FakeHost(), client=client)

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    out = capsys.readouterr().out
    assert code == 0
    assert client.secrets == {}
    assert "Skipped: GH_PAT: already exists; use --force to update" in out
    assert "Skipped: OPENROUTER_API_KEY: already exists; use --force to update" in out
    assert "not set (no value provided)" not in out


def test_existing_secrets_are_reported_from_the_outcome(monkeypatch, capsys):
    client = GhInitClient()
    client.existing = frozenset({"GH_PAT", "OPENROUTER_API_KEY"})
    prompter = FakePrompter(
        texts=["acme/widgets", "acme", "ubuntu-latest"], choices=["organization"]
    )
    _wire_pat(monkeypatch, FakeHost(), prompter, client=client)

    assert cli.main([]) == 0

    out = capsys.readouterr().out
    assert "Skipped: GH_PAT: already exists in acme" in out
    assert "Skipped: OPENROUTER_API_KEY: already exists in acme" in out
    assert prompter.secret_values == []  # no value is asked for a skipped secret
    assert client.secrets == {}


def test_force_rewrites_existing_secrets(monkeypatch):
    client = GhInitClient()
    client.existing = frozenset({"GH_PAT", "OPENROUTER_API_KEY"})
    _wire_pat(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive", "--force"])

    assert code == 0
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123", "GH_PAT": "pat-456"}


def test_force_skips_the_secret_probe(monkeypatch):
    host = FakeHost(existing_secrets=("GH_PAT",))
    client = GhInitClient()
    _wire_pat(monkeypatch, host, client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--non-interactive", "--force"]) == 0
    assert host.secret_probes == []  # --force makes the probe pointless


def test_non_interactive_does_not_require_the_skipped_secret_env(monkeypatch, capsys):
    client = GhInitClient()
    client.existing = frozenset({"GH_PAT"})
    _wire_pat(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123")

    code = cli.main(["--repo", "acme/widgets", "--org", "acme", "--non-interactive"])

    assert code == 0
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123"}
    assert "Skipped: GH_PAT: already exists" in capsys.readouterr().out


def test_repository_level_existing_secrets_are_skipped_too(monkeypatch, capsys):
    client = GhInitClient()
    client.existing = frozenset({"OPENROUTER_API_KEY"})
    _wire_pat(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    assert code == 0
    assert client.secrets == {"GH_PAT": "pat-456"}
    assert "Skipped: OPENROUTER_API_KEY: already exists" in capsys.readouterr().out


def test_a_failing_secret_listing_fails_the_run(monkeypatch, capsys):
    client = GhInitClient()
    client.raise_on_list = True
    _wire_pat(monkeypatch, FakeHost(), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--org", "acme", "--non-interactive"])

    assert code == 1
    assert "boom" in capsys.readouterr().err
    assert client.pr_calls == [] and client.secrets == {}


# ── interactive mode ─────────────────────────────────────────────────────────


def test_interactive_flow_asks_for_everything(monkeypatch, capsys):
    client = GhInitClient()
    prompter = FakePrompter(
        texts=["acme/widgets", "self-hosted"],
        choices=["repository", "commandcode"],
        secrets=["cc-key", "pat-9"],
    )
    _wire_pat(monkeypatch, FakeHost(), prompter, client=client)

    code = cli.main([])

    captured = capsys.readouterr()
    assert code == 0
    assert client.secrets == {"COMMANDCODE_API_KEY": "cc-key", "GH_PAT": "pat-9"}
    assert 'runs_on: "self-hosted"' in client.committed[".github/workflows/nebuko.yaml"]
    assert "COMMANDCODE_API_KEY" in client.committed[".github/workflows/nebuko.yaml"]
    assert "personal-access-tokens" in captured.out
    assert "cc-key" not in captured.out and "pat-9" not in captured.out


def test_pat_is_skipped_when_the_app_is_installed(monkeypatch):
    client = GhInitClient()
    prompter = FakePrompter(
        texts=["acme/widgets"], choices=["repository", "openrouter"], secrets=["key-123"]
    )
    _wire(monkeypatch, FakeHost(app_installed=True), prompter, client=client)

    assert cli.main([]) == 0
    assert client.secrets == {"OPENROUTER_API_KEY": "key-123"}


def test_an_empty_secret_answer_skips_that_secret(monkeypatch, capsys):
    client = GhInitClient()
    prompter = FakePrompter(
        texts=["acme/widgets"], choices=["repository", "openrouter"], secrets=["", ""]
    )
    _wire_pat(monkeypatch, FakeHost(), prompter, client=client)

    assert cli.main([]) == 1  # nothing to write: every planned secret failed
    assert client.secrets == {}
    assert "no value provided" in capsys.readouterr().err


# ── prompter ─────────────────────────────────────────────────────────────────


def test_prompter_text_choice_secret_and_confirm():
    answers = iter(["", "2", "y"])
    prompter = cli.InitPrompter(
        input_fn=lambda prompt: next(answers), secret_fn=lambda prompt: "hidden"
    )
    assert prompter.ask_text("Repo", "acme/widgets") == "acme/widgets"
    assert (
        prompter.ask_choice("Provider", ["openrouter", "commandcode"], "openrouter")
        == "commandcode"
    )
    assert prompter.confirm("Overwrite?") is True
    assert prompter.ask_secret("GH_PAT") == "hidden"


def test_prompter_choice_accepts_a_name_and_retries(capsys):
    answers = iter(["bogus", "opencode"])
    prompter = cli.InitPrompter(input_fn=lambda prompt: next(answers))
    assert prompter.ask_choice("Provider", ["openrouter", "opencode"], "openrouter") == "opencode"
    assert "Please answer with a number" in capsys.readouterr().out


def test_prompter_confirm_uses_the_default_on_an_empty_answer():
    prompter = cli.InitPrompter(input_fn=lambda prompt: "")
    assert prompter.confirm("Overwrite?", default=False) is False
    assert prompter.confirm("Overwrite?", default=True) is True


def test_non_interactive_prompter_never_confirms():
    assert cli.NonInteractivePrompter().confirm("Overwrite?") is False


# ── dispatch ─────────────────────────────────────────────────────────────────


def test_run_loop_dispatches_init(capsys):
    assert run_loop_main(["init", "--non-interactive"]) == 2
    assert "--repo is required" in capsys.readouterr().err
