"""Orchestration contract for ``nebuko init`` (issue #1272, rev 9)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from nebuko.engine.init import setup
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
    """Stand-in host whose secret listing fails (decision 9 / R9)."""

    def existing_paths(self, target, paths):
        return set()

    def existing_secrets(self, target):
        raise RuntimeError("listing failed")

    def open_pr(self, target, branch, title, body, files):
        return "https://github.com/acme/widgets/pull/7"

    def set_secret(self, target, name, value):
        pass


def _answers() -> InitAnswers:
    return InitAnswers(
        target=SetupTarget(host="github.com", owner="acme", repo="widgets"),
        provider=_Profile(),
        runs_on="ubuntu-latest",
        pin_ref="v1.2.3",
    )


# Given a host whose secret listing raises, when run_init runs, then the
# exception propagates (no silent empty set, no accidental "create" plan)
def test_run_init_propagates_existing_secrets_failure():
    with pytest.raises(RuntimeError):
        setup.run_init(_answers(), source=FakeSource(), host=RaisingHost(), prompter=None)


# Given the PR 1 broker removal, when setup.py is inspected, then no broker
# symbols remain (no RepoVariableWriter, no broker URL/variable)
def test_run_init_no_broker_references():
    source = Path(setup.__file__).read_text(encoding="utf-8")
    for symbol in ("RepoVariableWriter", "broker_url", "BROKER_VARIABLE", "DEFAULT_BROKER_URL"):
        assert symbol not in source
