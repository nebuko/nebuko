"""``nebuko run agent --name <agent>`` — start one named agent from a terminal (issue #838).

The grammar and overrides are translated to the existing per-invocation env contract
(``AGENT_CONFIG_PATH``, ``CONFIG_COMPLEXITY``, ``CONFIG_TEMPERATURE``) so the ``@@``
shortcut can reuse :class:`AgentRunRequest`. Exit codes: ``0`` success, ``1`` run
failure, ``2`` usage (unknown agent, bad arguments).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Literal, Sequence

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
    # Single-agent execution is not wired yet; the validated request and its env are the
    # contract the execution step will consume.
    print(
        f"nebuko run agent: single-agent execution is not implemented yet "
        f"(agent={request.name}, env={request.to_env()})",
        file=sys.stderr,
    )
    return EXIT_FAILED
