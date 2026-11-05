"""Orchestration contract for ``nebuko init`` (issue #1272, rev 9)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from nebuko.engine.init import setup
from nebuko.engine.init.core import AGENT_CONFIG_PATH, WORKFLOW_PATH
from nebuko.engine.init.ports import InitAnswers, ProviderProfile, SetupTarget


class _Profile(ProviderProfile):
    name = "openrouter"
    description = "OpenRouter (default)"
    template_path = "defaults/agents/agent-config-openrouter.yaml"
    secret_names = ("OPENROUTER_API_KEY",)


class FakeSource:
    def fetch(self, path, ref):
        return f"content-of-{path}"


class RaisingHost:
    """Stand-in client whose secret listing fails (decision 9 / R9)."""

    def get_existing_files(self, target, paths):
        return {}

    def existing_secrets(self, target):
        raise RuntimeError("listing failed")

    def get_or_open_pull_request(self, target, branch, title, body):
        return "https://github.com/acme/widgets/pull/7"

    def put_secret(self, target, name, value, visibility=None, repos=None):
        pass


def _answers() -> InitAnswers:
    return InitAnswers(
        target=SetupTarget(host="github.com", owner="acme", repo="widgets"),
        provider=_Profile(),
        runs_on="ubuntu-latest",
        pin_ref="v1.2.3",
    )


class ProbeHostBase:
    """Minimal client stand-in; tests override the failing method."""

    def get_existing_files(self, target, paths):
        return {}

    def existing_secrets(self, target):
        return frozenset()

    def upsert_branch_and_commit(self, target, branch, files):
        pass

    def get_or_open_pull_request(self, target, branch, title, body):
        return "https://github.com/acme/widgets/pull/7"

    def put_secret(self, target, name, value, visibility=None, repos=None):
        pass


class ConfirmingPrompter:
    def confirm(self, label, default=False):
        return True

    def ask_secret(self, name):
        return "value"


# Given a host whose secret listing raises, when run_init runs, then the
# exception propagates (no silent empty set, no accidental "create" plan)
def test_run_init_propagates_existing_secrets_failure():
    with pytest.raises(RuntimeError):
        setup.run_init(
            _answers(),
            source=FakeSource(),
            host=RaisingHost(),
            prompter=None,
            pat_mode=False,
            non_interactive=False,
        )


# Given the closing message is built from the PR URL and slug, when formatted,
# then it names the PR, the try-it comment and the sign-off (moved from
# test_render.py, N40 — the rest of that file pins the plan_init render,
# already covered by test_core.py)
def test_next_steps_message_names_the_pull_request_and_the_slug():
    message = setup.next_steps_message("https://github.com/acme/widgets/pull/7", "acme/widgets")
    assert "Next steps" in message
    assert "1. Review and merge the pull request: https://github.com/acme/widgets/pull/7" in message
    assert 'comment "@@dev Hello Nebuko!" on an issue in acme/widgets.' in message
    assert message.endswith("Let's build something with Nebuko's agent teams 🤝")


# Given run_init's signature, when inspected, then the client parameter is
# required and typed GitHubInitClient with no Optional/default (N39)
def test_run_init_host_parameter_is_required_github_init_client():
    signature = inspect.signature(setup.run_init)
    host_param = signature.parameters["host"]
    assert host_param.default is inspect.Parameter.empty
    assert "GitHubInitClient" in str(host_param.annotation)
    assert "None" not in str(host_param.annotation)


# Given a pre-existing agent config in the repo, when run_init plans, then the
# probed path, the prompted-path context, the written path and the workflow's
# ${agent_config_path} are all AGENT_CONFIG_PATH (N37 — exercises setup.py's
# probe choice, which a plan_init-level test cannot)
def test_run_init_probed_prompted_and_written_path_is_agent_config_path():
    recorded: dict[str, object] = {}

    class ProbeHost:
        def get_existing_files(self, target, paths):
            recorded["probe"] = list(paths)
            return {AGENT_CONFIG_PATH: True}

        def existing_secrets(self, target):
            return frozenset()

        def upsert_branch_and_commit(self, target, branch, files):
            recorded["written"] = sorted(files)
            recorded["workflow"] = files[WORKFLOW_PATH]

        def get_or_open_pull_request(self, target, branch, title, body):
            return "https://github.com/acme/widgets/pull/7"

        def put_secret(self, target, name, value, visibility=None, repos=None):
            recorded.setdefault("secrets", []).append(name)

    class ConfirmingPrompter:
        def confirm(self, label, default=False):
            recorded["confirm"] = label
            return True

        def ask_secret(self, name):
            return "value"

    outcome = setup.run_init(
        _answers(),
        source=FakeSource(),
        host=ProbeHost(),
        prompter=ConfirmingPrompter(),
        pat_mode=False,
        non_interactive=False,
    )
    assert recorded["probe"] == [AGENT_CONFIG_PATH, WORKFLOW_PATH]
    assert AGENT_CONFIG_PATH in recorded["confirm"]
    assert recorded["written"] == [AGENT_CONFIG_PATH, WORKFLOW_PATH]
    assert f"${{agent_config_path}}" not in recorded["workflow"]
    assert AGENT_CONFIG_PATH in recorded["workflow"]
    assert outcome.code == setup.EXIT_OK


# Given a client whose file probe raises something other than a 404, when
# run_init runs, then the error propagates (only 404 ⇒ absent, N13)
def test_run_init_propagates_non_404_probe_errors():
    class ExplodingProbeHost(ProbeHostBase):
        def get_existing_files(self, target, paths):
            raise RuntimeError("gh api failed: 500")

    with pytest.raises(RuntimeError):
        setup.run_init(
            _answers(),
            source=FakeSource(),
            host=ExplodingProbeHost(),
            prompter=None,
            pat_mode=False,
            non_interactive=False,
        )


# Given run_init completed, when the local checkout is inspected, then no file
# was written locally (N12 — everything goes to the nebuko/init branch)
def test_run_init_writes_no_local_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RecordingHost:
        def get_existing_files(self, target, paths):
            return {}

        def existing_secrets(self, target):
            return frozenset()

        def upsert_branch_and_commit(self, target, branch, files):
            pass

        def get_or_open_pull_request(self, target, branch, title, body):
            return "https://github.com/acme/widgets/pull/7"

        def put_secret(self, target, name, value, visibility=None, repos=None):
            pass

    setup.run_init(
        _answers(),
        source=FakeSource(),
        host=RecordingHost(),
        prompter=ConfirmingPrompter(),
        pat_mode=False,
        non_interactive=False,
    )
    assert list(tmp_path.rglob("*")) == []  # nothing written to the checkout


# Given the PR 1 broker removal, when setup.py is inspected, then no broker
# symbols remain (no RepoVariableWriter, no broker URL/variable)
def test_run_init_no_broker_references():
    source = Path(setup.__file__).read_text(encoding="utf-8")
    for symbol in ("RepoVariableWriter", "broker_url", "BROKER_VARIABLE", "DEFAULT_BROKER_URL"):
        assert symbol not in source
