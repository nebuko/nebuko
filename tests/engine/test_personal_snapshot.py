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
load_config_mod = importlib.import_module("nebuko.engine.lib.config.features.load_config.load_config")

SHIPPED_OPENROUTER = Path(__file__).resolve().parents[2] / "defaults" / "agents" / "agent-config-openrouter.yaml"
SHIPPED_ENDPOINT = "https://openrouter.ai/api/v1"
PERSONAL_ENDPOINT = "https://personal.example/v1"
EVIL_ENDPOINT = "https://evil.example/v1"


@pytest.fixture
def personal_run(tmp_path, monkeypatch):
    """A personal-repo run whose checkout holds one agent config and a manifest naming it."""
    workspace = tmp_path / "ws"
    personal = workspace / ".nebuko-personal" / ".nebuko"
    (personal / "agents").mkdir(parents=True)
    (personal / "nebuko.yaml").write_text("agent_config: agent-config-personal.yaml\n")
    config_text = SHIPPED_OPENROUTER.read_text(encoding="utf-8").replace(SHIPPED_ENDPOINT, PERSONAL_ENDPOINT)
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


def _personal_agents(personal_run) -> Path:
    return personal_run / ".nebuko-personal" / ".nebuko" / "agents"


def test_a_mid_run_rewrite_does_not_reach_the_next_agent_invocation(personal_run, monkeypatch):
    personal = personal_run / ".nebuko-personal" / ".nebuko"
    config = personal / "agents" / "agent-config-personal.yaml"
    manifest = personal / "nebuko.yaml"

    with resolver.personal_snapshot_scope(personal_run):
        first = _select()
        assert first["agent_config_layer"] == "personal"
        first_llm = _agent_child_llm(
            monkeypatch, first["agent_config_path"], resolver.personal_snapshot_child_env(first["agent_config_path"])
        )

        # The dev agent's step rewrites the endpoint and repoints the manifest.
        config.write_text(config.read_text(encoding="utf-8").replace(PERSONAL_ENDPOINT, EVIL_ENDPOINT))
        manifest.write_text("agent_config: agent-config-evil.yaml\n", encoding="utf-8")

        second = _select()
        assert second["agent_config_raw"] == "agent-config-personal.yaml"
        assert second["agent_config_path"] == first["agent_config_path"]
        second_llm = _agent_child_llm(
            monkeypatch, second["agent_config_path"], resolver.personal_snapshot_child_env(second["agent_config_path"])
        )

    assert first_llm["base_url"].startswith(PERSONAL_ENDPOINT)
    assert second_llm["base_url"] == first_llm["base_url"]


def test_a_shipped_name_is_not_shadowed_by_a_file_created_mid_run(personal_run, monkeypatch):
    personal = personal_run / ".nebuko-personal" / ".nebuko"
    (personal / "nebuko.yaml").write_text("agent_config: agent-config-openrouter.yaml\n")

    with resolver.personal_snapshot_scope(personal_run):
        first = _select()
        assert first["agent_config_layer"] == "shipped"
        first_llm = _agent_child_llm(
            monkeypatch, first["agent_config_path"], resolver.personal_snapshot_child_env(first["agent_config_path"])
        )

        # A dev agent creates a file with the shipped name in the personal agents directory.
        shadow = _personal_agents(personal_run) / "agent-config-openrouter.yaml"
        shadow.write_text(SHIPPED_OPENROUTER.read_text(encoding="utf-8").replace(SHIPPED_ENDPOINT, EVIL_ENDPOINT))

        second = _select()
        assert second["agent_config_layer"] == "shipped"
        assert second["agent_config_path"] == first["agent_config_path"]
        second_llm = _agent_child_llm(
            monkeypatch, second["agent_config_path"], resolver.personal_snapshot_child_env(second["agent_config_path"])
        )

    assert first_llm["base_url"] == SHIPPED_ENDPOINT
    assert second_llm["base_url"] == SHIPPED_ENDPOINT


def test_an_unfrozen_personal_path_fails_loudly(personal_run, monkeypatch):
    with resolver.personal_snapshot_scope(personal_run):
        late = _personal_agents(personal_run) / "agent-config-late.yaml"
        late.write_text(SHIPPED_OPENROUTER.read_text(encoding="utf-8").replace(SHIPPED_ENDPOINT, EVIL_ENDPOINT))
        child_env = resolver.personal_snapshot_child_env(str(late))

        monkeypatch.setattr(resolver, "_CURRENT_SNAPSHOT", None)
        for key, value in child_env.items():
            monkeypatch.setenv(key, value)
        with pytest.raises(resolver.PersonalConfigNotFrozen):
            load_config_mod.load_config(str(late))
        with pytest.raises(Exception, match="frozen snapshot"):
            _agent_child_llm(monkeypatch, late, child_env)

        # Not on disk either: the snapshot still decides, nothing is read from the agents dir.
        late.unlink()
        with pytest.raises(resolver.PersonalConfigNotFrozen):
            _agent_child_llm(monkeypatch, late, child_env)


def test_a_frozen_config_is_read_from_the_snapshot_after_the_disk_copy_is_deleted(personal_run, monkeypatch):
    with resolver.personal_snapshot_scope(personal_run):
        first = _select()
        Path(first["agent_config_path"]).unlink()
        assert _select()["agent_config_layer"] == "personal"
        llm = _agent_child_llm(
            monkeypatch, first["agent_config_path"], resolver.personal_snapshot_child_env(first["agent_config_path"])
        )
    assert llm["base_url"].startswith(PERSONAL_ENDPOINT)


def test_the_child_env_carries_only_the_selected_config(personal_run):
    (_personal_agents(personal_run) / "agent-config-other.yaml").write_text(
        "# " + "x" * 200_000 + "\n", encoding="utf-8"
    )
    with resolver.personal_snapshot_scope(personal_run):
        selected = _select()
        raw = resolver.personal_snapshot_child_env(selected["agent_config_path"])[resolver.PERSONAL_SNAPSHOT_ENV]
    assert len(raw.encode("utf-8")) < resolver.PERSONAL_SNAPSHOT_ENV_LIMIT
    assert "agent-config-other.yaml" not in raw


def test_an_oversized_child_config_fails_with_a_clear_message(personal_run):
    (_personal_agents(personal_run) / "agent-config-personal.yaml").write_text(
        "# " + "x" * resolver.PERSONAL_SNAPSHOT_ENV_LIMIT + "\n", encoding="utf-8"
    )
    with resolver.personal_snapshot_scope(personal_run):
        selected = _select()
        with pytest.raises(resolver.PersonalSnapshotTooLarge, match="environment limit"):
            resolver.personal_snapshot_child_env(selected["agent_config_path"])


def test_a_missing_secret_is_checked_against_the_frozen_config(personal_run, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with resolver.personal_snapshot_scope(personal_run):
        out = _select()
    assert "config_command_error" in out
    assert "OPENROUTER_API_KEY" in out["config_command_error"]


def test_a_config_that_exists_only_in_the_personal_repo_is_named_personal(personal_run):
    with resolver.personal_snapshot_scope(personal_run):
        out = _select()
    assert "config_command_error" not in out
    assert out["agent_config_layer"] == "personal"
    assert out["agent_config_raw"] == "agent-config-personal.yaml"


def test_a_project_run_has_no_personal_snapshot(personal_run, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    with resolver.personal_snapshot_scope(personal_run):
        assert resolver.current_personal_snapshot() is None
        assert resolver.personal_snapshot_child_env() == {}
        assert resolver.frozen_agent_config_text(personal_run / "agent-config.yaml") is None
