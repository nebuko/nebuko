"""``python -m engine lint`` — the offline workflow-YAML lint command (issue #751).

Kept separate from ``nebuko.engine.lint`` (the pure rule engine) and ``nebuko.engine.run_loop``
(the adapter-driven runner) so each module keeps a single responsibility. The
exit-code contract is the CI interface: ``0`` clean (or warnings only), ``1`` at
least one error, ``2`` usage or load failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Sequence

import yaml

from nebuko.engine.lint import LintIssue, WorkflowLinter

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_USAGE = 2

_WORKFLOW_SUFFIXES = (".yaml", ".yml")


def _collect_targets(arguments: Sequence[str]) -> list[Path]:
    """Expand directory arguments to their workflow YAML files; keep file args."""
    targets: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_dir():
            for suffix in _WORKFLOW_SUFFIXES:
                targets.extend(sorted(path.glob(f"*{suffix}")))
        else:
            targets.append(path)
    return targets


def _format_issue(issue: LintIssue) -> str:
    step = f" step={issue.step}" if issue.step else ""
    location = f" ({issue.location})" if issue.location else ""
    return f"[{issue.rule}] {issue.severity}{step} {issue.message}{location}"


def _print_text(issues: Iterable[LintIssue]) -> None:
    issues = list(issues)
    for issue in issues:
        print(_format_issue(issue))
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = len(issues) - errors
    print(f"{len(issues)} issue(s): {errors} error(s), {warnings} warning(s)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m engine lint",
        description="Lint workflow YAML for graph-level defects (issue #751).",
    )
    parser.add_argument("paths", nargs="+", help="workflow YAML file(s) or directory")
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
    targets = _collect_targets(args.paths)
    if not targets:
        print("lint: no workflow files found", file=sys.stderr)
        return EXIT_USAGE

    try:
        issues = WorkflowLinter(strict=args.strict).lint_all(targets)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"lint: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.format == "json":
        print(json.dumps([asdict(issue) for issue in issues], indent=2))
    else:
        _print_text(issues)

    return EXIT_ERRORS if any(issue.severity == "error" for issue in issues) else EXIT_OK
