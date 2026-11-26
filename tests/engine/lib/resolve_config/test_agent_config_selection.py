"""Agent-config name fallback for a persisted name that no longer resolves (#1479)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

rc = importlib.import_module("nebuko.engine.lib.resolve_config.resolve_config")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".nebuko").mkdir(parents=True)
    (ws / ".nebuko" / "nebuko.yaml").write_text("agent_config: agent-config-mistral.yaml\n")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(ws))
    return ws


def _select(prev_name, personal_lane, flag=None):
    return rc._agent_config_selection(
        "",
        None,
        SimpleNamespace(agent_config=prev_name),
        requested_config=flag,
        lane_action="resume",
        personal_lane=personal_lane,
    )


def test_project_lane_missing_previous_falls_back_to_manifest(workspace, capsys):
    out = _select("gone.yaml", personal_lane=False)
    assert "config_command_error" not in out
    assert out["agent_config_raw"] == "agent-config-mistral.yaml"
    assert "persisted agent config 'gone.yaml' not found" in capsys.readouterr().err


def test_personal_lane_missing_previous_falls_back_to_shipped_default(workspace, monkeypatch, capsys):
    personal = workspace / ".nebuko-personal" / ".nebuko"
    personal.mkdir(parents=True)
    (personal / "nebuko.yaml").write_text("agent_config: agent-config-openrouter.yaml\n")
    out = _select("gone.yaml", personal_lane=True)
    assert "config_command_error" not in out
    assert out["agent_config_raw"] == "agent-config-openrouter.yaml"
    assert out["agent_config_layer"] == "shipped"
    assert "persisted agent config 'gone.yaml' not found" in capsys.readouterr().err
