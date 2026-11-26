"""The personal agent config is read once per run and frozen for every agent child (issue #1485)."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

resolver = importlib.import_module(
    "nebuko.engine.lib.resolve_config.features.resolve_route.agent_config_resolver"
)
rc = importlib.import_module("nebuko.engine.lib.resolve_config.resolve_config")
resolve_llm = importlib.import_module(
    "nebuko.engine.lib.agent_script.features.resolve_llm_config.resolve_llm_config"
)

SHIPPED_OPENROUTER = Path(__file__).resolve().parents[2] / "defaults" / "agents" / "agent-config-openrouter.yaml"
PERSONAL_ENDPOINT = "https://personal.example/v1"


@pytest.fixture
def personal_run(tmp_path, monkeypatch):
    """A personal-repo run whose checkout holds one agent config and a manifest naming it."""
    workspace = tmp_path / "ws"
    personal = workspace / ".nebuko-personal" / ".nebuko"
    (personal / "agents").mkdir(parents=True)
    (personal / "nebuko.yaml").write_text("agent_config: agent-config-personal.yaml\n")
    config_text = SHIPPED_OPENROUTER.read_text(encoding="utf-8").replace(
        "https://openrouter.ai/api/v1", PERSONAL_ENDPOINT
    )
    (personal / "agents" / "agent-config-personal.yaml").write_text(config_text, encoding="utf-8")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("GITHUB_REPOSITORY", "alice/nebuko-personal")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ROLE_NAME", "architect")
    monkeypatch.delenv(resolver.PERSONAL_SNAPSHOT_ENV, raising=False)
    monkeypatch.delenv("AGENT_CONFIG_PATH", raising=False)
    monkeypatch.setattr(resolver, "_CURRENT_SNAPSHOT", None)
    return workspace


def _select(requested=None):
    return rc._agent_config_selection(
        "",
        None,
        None,
        requested_config=requested,
        lane_action="allow",
        personal_lane=True,
    )


def _agent_child_llm(monkeypatch, config_path, child_env):
    """The LLM config an agent child resolves: fresh process state, only the engine's hand-off env."""
    monkeypatch.setattr(resolver, "_CURRENT_SNAPSHOT", None)
    for key, value in child_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AGENT_CONFIG_PATH", str(config_path))
    llm_config, _prompt, _agent = resolve_llm.resolve_llm_config(SimpleNamespace(), "")
    return llm_config


def test_a_mid_run_rewrite_does_not_reach_the_next_agent_invocation(personal_run, monkeypatch):
    personal = personal_run / ".nebuko-personal" / ".nebuko"
    config = personal / "agents" / "agent-config-personal.yaml"
    manifest = personal / "nebuko.yaml"

    with resolver.personal_snapshot_scope(personal_run):
        first = _select()
        assert first["agent_config_layer"] == "personal"
        first_llm = _agent_child_llm(monkeypatch, first["agent_config_path"], resolver.personal_snapshot_child_env())

        # The dev agent's step rewrites the endpoint and repoints the manifest.
        config.write_text(config.read_text(encoding="utf-8").replace(PERSONAL_ENDPOINT, "https://evil.example/v1"))
        manifest.write_text("agent_config: agent-config-evil.yaml\n", encoding="utf-8")

        second = _select()
        assert second["agent_config_raw"] == "agent-config-personal.yaml"
        assert second["agent_config_path"] == first["agent_config_path"]
        second_llm = _agent_child_llm(monkeypatch, second["agent_config_path"], resolver.personal_snapshot_child_env())

    assert first_llm["base_url"].startswith(PERSONAL_ENDPOINT)
    assert second_llm["base_url"] == first_llm["base_url"]


def test_a_config_that_exists_only_in_the_personal_repo_is_named_personal(personal_run):
    with resolver.personal_snapshot_scope(personal_run):
        out = _select()
    assert "config_command_error" not in out
    assert out["agent_config_layer"] == "personal"
    assert out["agent_config_raw"] == "agent-config-personal.yaml"


def test_a_missing_secret_is_checked_against_the_frozen_config(personal_run, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with resolver.personal_snapshot_scope(personal_run):
        out = _select()
    assert "config_command_error" in out
    assert "OPENROUTER_API_KEY" in out["config_command_error"]


def test_a_project_run_has_no_personal_snapshot(personal_run, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    with resolver.personal_snapshot_scope(personal_run):
        assert resolver.current_personal_snapshot() is None
        assert resolver.personal_snapshot_child_env() == {}
        assert resolver.frozen_agent_config_text(personal_run / "agent-config.yaml") is None
