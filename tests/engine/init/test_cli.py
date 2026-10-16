"""CLI contract for ``nebuko init`` (issue #991).

The adapters are faked, so these tests pin the command surface: the 0/1/2 exit
codes, the non-interactive environment mode, the no-overwrite rule, and the
property that no secret value ever reaches the output or the pull request.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.init import cli
from nebuko.engine.init.ports import PreflightReport, SetupHost
from nebuko.engine.init.refs import StaticRefResolver
from nebuko.engine.init.setup import SetupContext
from nebuko.engine.init.sources import FileTemplateSource
from nebuko.engine.run_loop import main as run_loop_main

REPO_ROOT = Path(__file__).resolve().parents[3]
PIN = "v1.2.3"
PR_URL = "https://github.com/acme/widgets/pull/7"


class FakeHost(SetupHost):
    def __init__(self, report=None, existing=(), app_installed=False, gh_available=True):
        self.report = report or PreflightReport(ok=True, missing_scopes=(), remediation="")
        self.existing = set(existing)
        self.app = app_installed
        self.gh = gh_available
        self.prs: list[dict] = []
        self.secrets: dict[str, str] = {}

    def gh_available(self):
        return self.gh

    def preflight(self, target):
        return self.report

    def app_installed(self, target):
        return self.app

    def existing_paths(self, target, paths):
        return {path for path in paths if path in self.existing}

    def open_pr(self, target, branch, title, body, files):
        self.prs.append(
            {"target": target, "branch": branch, "title": title, "body": body, "files": files}
        )
        return PR_URL

    def set_secret(self, target, name, value):
        self.secrets[name] = value


class FakePrompter:
    def __init__(self, texts=(), choice="", secrets=(), confirm_answer=False):
        self.texts = list(texts)
        self.choice = choice
        self.secret_values = list(secrets)
        self.confirm_answer = confirm_answer
        self.confirmations: list[str] = []

    def ask_text(self, label, default=""):
        return self.texts.pop(0) if self.texts else default

    def ask_choice(self, label, options, default=""):
        return self.choice or default

    def ask_secret(self, name):
        return self.secret_values.pop(0) if self.secret_values else ""

    def confirm(self, label, default=False):
        self.confirmations.append(label)
        return self.confirm_answer


def _wire(monkeypatch, host, prompter=None):
    context = SetupContext(
        source=FileTemplateSource(REPO_ROOT), refs=StaticRefResolver(PIN), host=host
    )
    monkeypatch.setattr(cli, "build_setup", lambda **kwargs: context)
    if prompter is not None:
        monkeypatch.setattr(cli, "InitPrompter", lambda: prompter)
    return context


def _set_secrets(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(f"NEBUKO_SECRET_{name}", value)


# ── non-interactive mode ─────────────────────────────────────────────────────


def test_non_interactive_opens_a_pr_and_sets_the_secrets(monkeypatch, capsys):
    host = FakeHost()
    _wire(monkeypatch, host)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--provider", "openrouter", "--non-interactive"])

    captured = capsys.readouterr()
    assert code == 0
    assert host.secrets == {"OPENROUTER_API_KEY": "key-123", "GH_PAT": "pat-456"}
    assert host.prs[0]["branch"] == "nebuko/init"
    assert PR_URL in captured.out
    assert "key-123" not in captured.out + captured.err
    assert "pat-456" not in captured.out + captured.err


def test_the_pull_request_carries_the_config_and_the_pinned_workflow(monkeypatch):
    host = FakeHost()
    _wire(monkeypatch, host)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--non-interactive"]) == 0

    files = host.prs[0]["files"]
    assert files.agent_config_path == ".nebuko/agents/agent-config.yaml"
    assert files.workflow_path == ".github/workflows/nebuko.yaml"
    assert "OPENROUTER_API_KEY" in files.agent_config
    assert f"nebuko-workflow.yaml@{PIN}" in files.workflow
    assert "key-123" not in files.workflow + files.agent_config + host.prs[0]["body"]


def test_missing_workflow_scope_exits_two_with_the_remediation(monkeypatch, capsys):
    host = FakeHost(
        report=PreflightReport(
            ok=False, missing_scopes=("workflow",), remediation="gh auth refresh -s workflow"
        )
    )
    _wire(monkeypatch, host)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    assert code == 2
    assert "gh auth refresh -s workflow" in capsys.readouterr().err
    assert host.prs == []
    assert host.secrets == {}


# ── gh availability ──────────────────────────────────────────────────────────


def test_missing_gh_cli_exits_two_with_an_install_hint(monkeypatch, capsys):
    host = FakeHost(gh_available=False)
    _wire(monkeypatch, host)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    err = capsys.readouterr().err
    assert code == 2
    assert "GitHub CLI (gh)" in err
    assert "cli.github.com" in err
    assert host.prs == []
    assert host.secrets == {}


def test_missing_gh_cli_fails_before_asking_anything(monkeypatch, capsys):
    host = FakeHost(gh_available=False)
    prompter = FakePrompter(texts=["acme/widgets"], choice="openrouter", secrets=["key", "pat"])
    _wire(monkeypatch, host, prompter)

    assert cli.main([]) == 2
    assert prompter.texts == ["acme/widgets"]  # the check runs before any prompt
    assert "GitHub CLI (gh)" in capsys.readouterr().err


def test_non_interactive_requires_the_secret_env_vars(monkeypatch, capsys):
    host = FakeHost()
    _wire(monkeypatch, host)
    monkeypatch.delenv("NEBUKO_SECRET_OPENROUTER_API_KEY", raising=False)

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    assert code == 2
    assert "NEBUKO_SECRET_OPENROUTER_API_KEY" in capsys.readouterr().err
    assert host.prs == []


def test_non_interactive_requires_the_repo_flag(monkeypatch, capsys):
    _wire(monkeypatch, FakeHost())
    assert cli.main(["--non-interactive"]) == 2
    assert "--repo is required" in capsys.readouterr().err


def test_org_flag_targets_org_level_secrets(monkeypatch):
    host = FakeHost()
    _wire(monkeypatch, host)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--org", "acme", "--non-interactive"])

    assert code == 0
    assert host.prs[0]["target"].org == "acme"


def test_unknown_provider_is_a_usage_error(monkeypatch):
    _wire(monkeypatch, FakeHost())
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--repo", "acme/widgets", "--provider", "nope", "--non-interactive"])
    assert excinfo.value.code == 2


# ── idempotence ──────────────────────────────────────────────────────────────


def test_existing_config_is_not_overwritten_without_confirmation(monkeypatch, capsys):
    host = FakeHost(existing=(".nebuko/agents/agent-config.yaml",))
    _wire(monkeypatch, host)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive"])

    assert code == 1
    assert host.prs == []
    assert "left untouched" in capsys.readouterr().err


def test_force_overwrites_existing_config(monkeypatch):
    host = FakeHost(existing=(".github/workflows/nebuko.yaml",))
    _wire(monkeypatch, host)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    code = cli.main(["--repo", "acme/widgets", "--non-interactive", "--force"])

    assert code == 0
    assert len(host.prs) == 1


def test_interactive_run_asks_before_overwriting(monkeypatch, capsys):
    host = FakeHost(existing=(".nebuko/agents/agent-config.yaml",))
    prompter = FakePrompter(
        texts=["acme/widgets", "ubuntu-latest"],
        choice="openrouter",
        secrets=["key-123", "pat-456"],
        confirm_answer=True,
    )
    _wire(monkeypatch, host, prompter)

    assert cli.main([]) == 0
    assert prompter.confirmations
    assert len(host.prs) == 1


# ── interactive mode ─────────────────────────────────────────────────────────


def test_interactive_flow_asks_for_everything(monkeypatch, capsys):
    host = FakeHost()
    prompter = FakePrompter(
        texts=["acme/widgets", "self-hosted"],
        choice="commandcode",
        secrets=["cc-key", "pat-9"],
    )
    _wire(monkeypatch, host, prompter)

    code = cli.main([])

    captured = capsys.readouterr()
    assert code == 0
    assert host.secrets == {"COMMANDCODE_API_KEY": "cc-key", "GH_PAT": "pat-9"}
    workflow = host.prs[0]["files"].workflow
    assert 'runs_on: "self-hosted"' in workflow
    assert "COMMANDCODE_API_KEY" in workflow
    assert "personal-access-tokens" in captured.out
    assert "cc-key" not in captured.out and "pat-9" not in captured.out


def test_pat_is_skipped_when_the_app_is_installed(monkeypatch):
    host = FakeHost(app_installed=True)
    prompter = FakePrompter(texts=["acme/widgets"], choice="openrouter", secrets=["key-123"])
    _wire(monkeypatch, host, prompter)

    assert cli.main([]) == 0
    assert host.secrets == {"OPENROUTER_API_KEY": "key-123"}


def test_an_empty_secret_answer_skips_that_secret(monkeypatch, capsys):
    host = FakeHost()
    prompter = FakePrompter(texts=["acme/widgets"], choice="openrouter", secrets=["", ""])
    _wire(monkeypatch, host, prompter)

    assert cli.main([]) == 0
    assert host.secrets == {}
    assert "skipping" in capsys.readouterr().out


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
