"""CLI-contract tests for ``python -m engine lint`` (issue #751).

The linter rules are pinned in ``test_lint.py``; these tests cover the command
surface the plan fixes — argument handling, the 0/1/2 exit-code contract, the
``--strict`` promotion, and the JSON/text output formats.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.lint_cli import main
from engine.run_loop import main as run_loop_main

FIXTURES = Path(__file__).parent / "lint_fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _fixture(name: str) -> str:
    return str(FIXTURES / name)


def test_clean_workflow_exits_zero(capsys):
    assert main([_fixture("clean.yaml")]) == 0
    assert "0 issue(s)" in capsys.readouterr().out


def test_errors_exit_one_and_are_reported(capsys):
    assert main([_fixture("l1_dangling_next.yaml")]) == 1
    assert "L1-dangling-next" in capsys.readouterr().out


def test_warnings_alone_exit_zero_unless_strict(capsys):
    assert main([_fixture("l2_unreachable.yaml")]) == 0
    assert main(["--strict", _fixture("l2_unreachable.yaml")]) == 1
    assert "L2-unreachable-step] error" in capsys.readouterr().out


def test_missing_file_exits_two(capsys):
    assert main([_fixture("does_not_exist.yaml")]) == 2
    assert "not found" in capsys.readouterr().err


def test_directory_argument_expands_to_workflow_files(capsys):
    assert main([str(FIXTURES)]) == 1
    assert "L7-no-terminal" in capsys.readouterr().out


def test_json_format_is_serialized_issues(capsys):
    assert main(["--format", "json", _fixture("l1_dangling_next.yaml")]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["rule"] == "L1-dangling-next"
    assert payload[0]["severity"] == "error"


def test_run_loop_main_dispatches_the_lint_subcommand():
    assert run_loop_main(["lint", _fixture("clean.yaml")]) == 0
    assert run_loop_main(["lint", _fixture("l1_dangling_next.yaml")]) == 1


def test_module_entrypoint_end_to_end():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "engine",
            "lint",
            "--strict",
            _fixture("l2_unreachable.yaml"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    assert "L2-unreachable-step" in result.stdout
