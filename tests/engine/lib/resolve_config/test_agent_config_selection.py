"""Agent-config name order for a run (#1479): --config, personal manifest, project manifest, default."""

from __future__ import annotations

import importlib

import pytest

rc = importlib.import_module("nebuko.engine.lib.resolve_config.resolve_config")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".nebuko").mkdir(parents=True)
    (ws / ".nebuko" / "nebuko.yaml").write_text("agent_config: agent-config-mistral.yaml\n")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(ws))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("AGENT_CONFIG_PATH", raising=False)
    return ws


def _select(personal_lane, flag=None):
    return rc._agent_config_selection("", None, requested_config=flag, personal_lane=personal_lane)


def test_project_lane_uses_project_manifest(workspace):
    out = _select(personal_lane=False)
    assert "config_command_error" not in out
    assert out["agent_config_raw"] == "agent-config-mistral.yaml"


def test_flag_beats_project_manifest(workspace):
    out = _select(personal_lane=False, flag="agent-config-openrouter.yaml")
    assert out["agent_config_raw"] == "agent-config-openrouter.yaml"


def test_personal_manifest_beats_project_manifest(workspace):
    personal = workspace / ".nebuko-personal" / ".nebuko"
    personal.mkdir(parents=True)
    (personal / "nebuko.yaml").write_text("agent_config: agent-config-openrouter.yaml\n")
    out = _select(personal_lane=True)
    assert out["agent_config_raw"] == "agent-config-openrouter.yaml"
    assert out["agent_config_layer"] == "shipped"


def test_personal_lane_never_reads_project_agent_dir(workspace):
    project_agents = workspace / ".nebuko" / "agents"
    project_agents.mkdir(parents=True)
    (project_agents / "project-only.yaml").write_text("{}\n")
    (workspace / ".nebuko" / "nebuko.yaml").write_text("agent_config: project-only.yaml\n")
    personal = workspace / ".nebuko-personal" / ".nebuko"
    personal.mkdir(parents=True)
    (personal / "nebuko.yaml").write_text("{}\n")
    out = _select(personal_lane=True)
    assert out.get("agent_config_layer") != "project"
