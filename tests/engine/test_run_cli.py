"""Tests for ``nebuko run agent`` (issue #838): parsing, name validation, env translation, dispatch."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nebuko.engine.lint_cli import main as lint_main
from nebuko.engine.models import Trigger
from nebuko.engine.run_cli import AgentRunRequest, build_parser, main
from nebuko.engine.run_loop import main as run_loop_main


def test_request_to_env_emits_only_set_overrides():
    assert AgentRunRequest(name="dev").to_env() == {}
    request = AgentRunRequest(
        name="dev", config="x.yaml", complexity="high", temperature=0.2, task="t"
    )
    assert request.to_env() == {
        "AGENT_CONFIG_PATH": "x.yaml",
        "CONFIG_COMPLEXITY": "high",
        "CONFIG_TEMPERATURE": "0.2",
    }


def test_parser_requires_name_and_rejects_bad_complexity():
    args = build_parser().parse_args(["agent", "--name", "dev", "--complexity", "low"])
    assert (args.resource, args.name, args.complexity) == ("agent", "dev", "low")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["agent"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["agent", "--name", "dev", "--complexity", "huge"])


def test_unknown_agent_exits_2(capsys):
    assert main(["agent", "--name", "does-not-exist"]) == 2
    assert "unknown agent 'does-not-exist'" in capsys.readouterr().err


def test_workflow_yaml_lints_clean(tmp_path):
    path = tmp_path / "agent-dev.yaml"
    path.write_text(AgentRunRequest(name="dev", complexity="low", task="say hi").workflow_yaml())
    assert lint_main([str(path)]) == 0


def test_trigger_payload_parses_as_trigger_and_selects_workflow():
    request = AgentRunRequest(name="dev")
    trigger = Trigger(**request.trigger_payload())
    assert trigger.body == "/nebuko run workflow --name agent-dev"
    assert "run: " not in request.workflow_yaml()
    assert "agent: \"dev\"" in request.workflow_yaml()
    assert "Run as the dev agent." in request.workflow_yaml()


def test_known_agent_executes_and_prints_answer(monkeypatch, capsys):
    seen: dict = {}

    def fake_run_loop_main(argv):
        run_dir = Path(argv[argv.index("--run-dir") + 1])
        seen["argv"] = argv
        seen["trigger"] = json.loads((run_dir / "trigger.json").read_text())
        seen["workflow"] = Path(os.environ["NEBUKO_STAGED_WORKFLOWS_DIR"], "agent-dev.yaml").read_text()
        seen["complexity"] = os.environ.get("CONFIG_COMPLEXITY")
        (run_dir / "latest_answer.md").write_text("hello from dev", encoding="utf-8")
        return 0

    monkeypatch.setattr("nebuko.engine.run_loop.main", fake_run_loop_main)
    monkeypatch.delenv("CONFIG_COMPLEXITY", raising=False)
    monkeypatch.setenv("NEBUKO_STAGED_WORKFLOWS_DIR", "/pinned/before")
    assert main(["agent", "--name", "dev", "--complexity", "high", "--task", "x"]) == 0
    assert "hello from dev" in capsys.readouterr().out
    assert seen["argv"][:2] == ["--adapter", "local"]
    assert seen["trigger"]["body"] == "/nebuko run workflow --name agent-dev"
    assert "complexity: high" in seen["workflow"]
    assert seen["complexity"] == "high"
    assert "CONFIG_COMPLEXITY" not in os.environ
    assert os.environ["NEBUKO_STAGED_WORKFLOWS_DIR"] == "/pinned/before"


def test_run_agent_restores_preexisting_env(monkeypatch):
    monkeypatch.setenv("CONFIG_TEMPERATURE", "0.9")
    monkeypatch.setattr("nebuko.engine.run_loop.main", lambda argv: 1)
    assert main(["agent", "--name", "dev", "--temperature", "0.1"]) == 1
    assert os.environ["CONFIG_TEMPERATURE"] == "0.9"


def test_run_loop_main_dispatches_run(capsys):
    assert run_loop_main(["run", "agent", "--name", "does-not-exist"]) == 2
    assert "unknown agent" in capsys.readouterr().err
