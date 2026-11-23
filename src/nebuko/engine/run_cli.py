"""``nebuko run agent --name <agent>`` — start one named agent from a terminal (issue #838).

The grammar and overrides are translated to the existing per-invocation env contract
(``AGENT_CONFIG_PATH``, ``CONFIG_COMPLEXITY``, ``CONFIG_TEMPERATURE``) so the ``@@``
shortcut can reuse :class:`AgentRunRequest`. Exit codes: ``0`` success, ``1`` run
failure, ``2`` usage (unknown agent, bad arguments).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from nebuko.engine.lib import default_paths
from nebuko.engine.lib.config.features.load_config.load_config import load_config

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

Complexity = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class AgentRunRequest:
    """One agent invocation requested from the command line or ``@@`` mention."""

    name: str
    config: str | None = None
    complexity: Complexity | None = None
    temperature: float | None = None
    task: str | None = None

    def to_env(self) -> dict[str, str]:
        """Translate the overrides to the engine's env contract; unset fields emit nothing."""
        env: dict[str, str] = {}
        if self.config:
            env["AGENT_CONFIG_PATH"] = self.config
        if self.complexity:
            env["CONFIG_COMPLEXITY"] = self.complexity
        if self.temperature is not None:
            env["CONFIG_TEMPERATURE"] = str(self.temperature)
        return env

    def workflow_yaml(self) -> str:
        """One-step workflow that runs ``name`` with the task as its step prompt."""
        prompt = self.task or f"Run as the {self.name} agent."
        return "\n".join(
            [
                f"name: {json.dumps(self.workflow_name)}",
                f"description: {json.dumps(f'Single run of agent {self.name} (nebuko run agent)')}",
                "steps:",
                "  - name: run",
                f"    agent: {json.dumps(self.name)}",
                f"    complexity: {self.complexity or 'medium'}",
                f"    prompt: {json.dumps(prompt)}",
                "",
            ]
        )

    def trigger_payload(self) -> dict[str, Any]:
        """``trigger.json`` body that selects the generated workflow by name."""
        return {
            "author": "nebuko-cli",
            "body": f"/nebuko run workflow --name {self.workflow_name}",
            "type": "workflow_dispatch",
            "number": 0,
        }

    @property
    def workflow_name(self) -> str:
        return f"agent-{self.name}"


def run_agent_request(request: AgentRunRequest) -> int:
    """Run ``request`` as a generated one-step workflow on the local adapter; print its answer.

    The engine resolves workflow names only from the caller's ``.nebuko/workflows/`` and the
    shipped defaults, so the generated file is staged there for the duration of the run and
    removed afterwards. The run state lives in a temporary run directory.
    """
    from nebuko.engine.lib.default_paths import user_dir
    from nebuko.engine.run_loop import main as run_loop_main

    workflows = user_dir() / "workflows"
    created_dir = not workflows.exists()
    workflows.mkdir(parents=True, exist_ok=True)
    workflow_file = workflows / f"{request.workflow_name}.yaml"
    overrides = {"ENGINE_ADAPTER": "local", **request.to_env()}
    saved = {key: os.environ.get(key) for key in overrides}
    with tempfile.TemporaryDirectory(prefix="nebuko-agent-") as tmp:
        run_dir = Path(tmp)
        (run_dir / "trigger.json").write_text(
            json.dumps(request.trigger_payload()), encoding="utf-8"
        )
        workflow_file.write_text(request.workflow_yaml(), encoding="utf-8")
        os.environ.update(overrides)
        try:
            code = run_loop_main(["--adapter", "local", "--run-dir", str(run_dir)])
        finally:
            workflow_file.unlink(missing_ok=True)
            if created_dir and not any(workflows.iterdir()):
                workflows.rmdir()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        answer = run_dir / "latest_answer.md"
        if answer.is_file():
            print(answer.read_text(encoding="utf-8"))
        return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nebuko run agent",
        description="Run one named agent with optional per-invocation overrides.",
    )
    parser.add_argument("resource", choices=("agent",), help="Resource to run (only 'agent').")
    parser.add_argument("--name", required=True, help="Agent name from the agent config.")
    parser.add_argument("--config", default=None, help="Agent config file (name or path).")
    parser.add_argument(
        "--complexity", choices=("low", "medium", "high"), default=None,
        help="Override the complexity tier.",
    )
    parser.add_argument("--temperature", type=float, default=None, help="Override temperature.")
    parser.add_argument("--task", default=None, help="Task text for the agent.")
    return parser


def _valid_agent_names(config: str | None) -> frozenset[str]:
    path = default_paths.config_path(config) if config else default_paths.agent_config_file()
    return frozenset(load_config(path).agents.keys())


def validate_agent_name(name: str, config: str | None) -> str | None:
    """Return an error message when ``name`` is not declared in the config, else ``None``."""
    try:
        names = _valid_agent_names(config)
    except Exception as error:  # noqa: BLE001 - surface any config failure as a usage error
        return f"could not load agent config: {error}"
    if name in names:
        return None
    return f"unknown agent '{name}'; valid agents: {', '.join(sorted(names)) or '<none>'}"


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    request = AgentRunRequest(
        name=args.name,
        config=args.config,
        complexity=args.complexity,
        temperature=args.temperature,
        task=args.task,
    )
    error = validate_agent_name(request.name, request.config)
    if error:
        print(f"nebuko run agent: {error}", file=sys.stderr)
        return EXIT_USAGE
    return run_agent_request(request)
