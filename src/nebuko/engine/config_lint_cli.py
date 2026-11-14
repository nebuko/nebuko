"""``python -m nebuko.engine lint-config`` — the offline agent-config lint command (issue #774).

Kept separate from ``nebuko.engine.config_lint`` (the pure rule engine) and
``nebuko.engine.run_loop`` (the adapter-driven runner) so each module keeps a
single responsibility. The exit-code contract is the CI interface: ``0`` clean
(or warnings only), ``1`` at least one error, ``2`` usage or load failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import yaml

from nebuko.engine.config_lint import ConfigLinter
from nebuko.engine.lint_cli import _print_text

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_USAGE = 2

_CONFIG_SUFFIXES = (".yaml", ".yml")


def default_target() -> str:
    """The shipped agent config, resolved through the layered lookup.

    Mirrors the runtime default: the repo's ``.nebuko/agents/agent-config.yaml``
    override when present, else the ``defaults/agents/`` copy shipped with Nebuko.
    """

    from nebuko.engine.lib.default_paths import agent_config_file

    return agent_config_file()


def _collect_targets(arguments: Sequence[str]) -> list[Path]:
    """Expand directory arguments to their YAML files; keep file arguments."""
    targets: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_dir():
            for suffix in _CONFIG_SUFFIXES:
                targets.extend(sorted(path.glob(f"*{suffix}")))
        else:
            targets.append(path)
    return targets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nebuko.engine lint-config",
        description="Lint agent-config YAML for config-level defects (issue #774).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="agent-config YAML file(s) or directory "
        "(default: the shipped agent-config.yaml)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="promote warnings to errors (also flips the exit code)",
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Lint the given paths and return the process exit code (0/1/2)."""
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    targets = _collect_targets(args.paths or [default_target()])
    if not targets:
        print("lint-config: no config files found", file=sys.stderr)
        return EXIT_USAGE

    try:
        issues = ConfigLinter(strict=args.strict).lint_all(targets)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"lint-config: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.format == "json":
        print(json.dumps([asdict(issue) for issue in issues], indent=2))
    else:
        _print_text(issues)

    return EXIT_ERRORS if any(issue.severity == "error" for issue in issues) else EXIT_OK
