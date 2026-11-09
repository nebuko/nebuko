"""App-aware ``nebuko init`` flow (issue #1025 → reworked for #1272).

Given the Nebuko App is installed, when ``init`` runs then no PAT prompt is
shown and the flow goes through the ``GitHubInitClient``. Without the App,
``init`` exits with the App-install guidance (or a ``--pat`` run keeps the PAT
path).
"""

from __future__ import annotations

from pathlib import Path

from nebuko.engine.init import cli
from nebuko.engine.init.core import AGENT_CONFIG_PATH
from nebuko.engine.init.ports import PreflightReport, SetupTarget
from nebuko.engine.init.refs import StaticRefResolver
from nebuko.engine.init.setup import SetupContext
from nebuko.engine.init.sources import FileTemplateSource

REPO_ROOT = Path(__file__).resolve().parents[3]
PIN = "v1.2.3"
PR_URL = "https://github.com/acme/widgets/pull/7"


class FakeHost:
    """Read-only host double: preflight, App detection and secret listing."""

    def __init__(self, app_installed=False):
        self.app = app_installed

    def gh_available(self):
        return True

    def preflight(self, target):
        return PreflightReport(ok=True, missing_scopes=(), remediation="")

    def app_installed(self, target):
        return self.app

    def existing_secrets(self, target):
        return frozenset()

    def get_existing_files(self, target, paths):
        return {}


class FakeClient:
    """``GitHubInitClient`` double: records the committed files and secrets."""

    def __init__(self):
        self.committed: dict[str, str] | None = None
        self.branch: str | None = None
        self.secrets: dict[str, object] = {}
        self.pr_title: str | None = None

    def get_existing_files(self, target, paths):
        return {}

    def existing_secrets(self, target):
        return frozenset()

    def upsert_branch_and_commit(self, target, branch, files):
        self.branch = branch
        self.committed = files

    def get_or_open_pull_request(self, target, branch, title, body):
        self.pr_title = title
        return PR_URL

    def put_secret(self, target, name, value):
        self.secrets[name] = value


class FakeResolver:
    def __init__(self, installed: bool):
        self.installed = installed
        self.probes = 0

    def is_installed(self):
        self.probes += 1
        return self.installed


class FakePrompter:
    def __init__(self, secrets=(), texts=(), choices=()):
        self.secret_values = list(secrets)
        self.texts = list(texts)
        self.choices = list(choices)
        self.secret_prompts: list[str] = []
        self.confirmations: list[str] = []

    def ask_text(self, label, default=""):
        return self.texts.pop(0) if self.texts else default

    def ask_choice(self, label, options, default=""):
        return self.choices.pop(0) if self.choices else default

    def ask_secret(self, name):
        self.secret_prompts.append(name)
        return self.secret_values.pop(0) if self.secret_values else ""

    def confirm(self, label, default=False):
        self.confirmations.append(label)
        return True


def _wire(monkeypatch, host, resolver=None, prompter=None, client=None):
    context = SetupContext(
        source=FileTemplateSource(REPO_ROOT),
        refs=StaticRefResolver(PIN),
        host=host,
        client=client or FakeClient(),
        resolver=resolver,
    )
    monkeypatch.setattr(cli, "build_setup", lambda **kwargs: context)
    if prompter is not None:
        monkeypatch.setattr(cli, "InitPrompter", lambda: prompter)
    return context


def _set_secrets(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(f"NEBUKO_SECRET_{name}", value)


def test_given_the_app_is_installed_when_init_runs_then_no_pat_prompt_appears(monkeypatch, capsys):
    client = FakeClient()
    prompter = FakePrompter(
        secrets=["key-123"], texts=["acme/widgets"], choices=["repository", "openrouter"]
    )
    _wire(monkeypatch, FakeHost(), FakeResolver(installed=True), prompter, client)

    assert cli.main([]) == 0

    assert "GH_PAT" not in prompter.secret_prompts
    assert set(client.secrets) == {"OPENROUTER_API_KEY"}
    assert "personal-access-tokens" not in capsys.readouterr().out


def test_given_the_app_is_installed_when_init_runs_then_the_files_go_to_the_init_branch(monkeypatch):
    client = FakeClient()
    _wire(monkeypatch, FakeHost(), FakeResolver(installed=True), client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123")

    assert cli.main(["--repo", "acme/widgets", "--non-interactive"]) == 0

    assert client.branch == "nebuko/init"
    assert set(client.committed) == {
        AGENT_CONFIG_PATH,
        ".github/workflows/nebuko.yaml",
    }


def test_given_the_app_is_not_installed_when_init_runs_then_the_app_install_is_requested(monkeypatch, capsys):
    prompter = FakePrompter(
        secrets=["key-123", "pat-456"], texts=["acme/widgets"], choices=["repository", "openrouter"]
    )
    _wire(monkeypatch, FakeHost(), FakeResolver(installed=False), prompter)

    assert cli.main([]) == 2

    err = capsys.readouterr().err
    assert "https://github.com/apps/nebuko/installations/new" in err
    assert "--pat" in err
    assert prompter.secret_prompts == []  # nothing is asked before the guidance


def test_given_the_app_is_not_installed_when_init_runs_with_pat_then_the_pat_is_asked(monkeypatch):
    client = FakeClient()
    prompter = FakePrompter(
        secrets=["key-123", "pat-456"], texts=["acme/widgets"], choices=["repository", "openrouter"]
    )
    _wire(monkeypatch, FakeHost(), FakeResolver(installed=False), prompter, client)

    assert cli.main(["--pat"]) == 0

    assert prompter.secret_prompts == ["OPENROUTER_API_KEY", "GH_PAT"]
    assert set(client.secrets) == {"OPENROUTER_API_KEY", "GH_PAT"}


def test_given_assume_app_when_init_runs_then_detection_is_skipped(monkeypatch):
    resolver = FakeResolver(installed=False)
    client = FakeClient()
    _wire(monkeypatch, FakeHost(), resolver, client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123")

    assert cli.main(["--repo", "acme/widgets", "--non-interactive", "--assume-app"]) == 0

    assert resolver.probes == 0
    assert "GH_PAT" not in client.secrets


def test_given_assume_pat_when_init_runs_then_the_pat_is_asked_despite_the_app(monkeypatch):
    resolver = FakeResolver(installed=True)
    client = FakeClient()
    _wire(monkeypatch, FakeHost(), resolver, client=client)
    _set_secrets(monkeypatch, OPENROUTER_API_KEY="key-123", GH_PAT="pat-456")

    assert cli.main(["--repo", "acme/widgets", "--non-interactive", "--pat"]) == 0

    assert resolver.probes == 0
    assert client.secrets["GH_PAT"] == "pat-456"


def test_given_the_pat_alias_when_parsing_then_it_maps_to_assume_pat():
    args = cli._build_parser().parse_args(["--pat"])

    assert args.assume_pat is True


def test_given_defaults_when_parsing_then_no_broker_flags_exist():
    args = cli._build_parser().parse_args(["--assume-app", "--force"])

    assert args.assume_app is True
    assert args.assume_pat is False
    assert args.force is True
