"""CLI-contract tests for ``python -m nebuko.engine lint-config`` (issue #774).

The linter rules are pinned in ``test_config_lint.py``; these tests cover the
command surface fixed by the plan — argument handling, the 0/1/2 exit-code
contract, ``--strict`` promotion, the default target, and the JSON/text formats.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from nebuko.engine.config_lint_cli import default_target, main
from nebuko.engine.run_loop import main as run_loop_main

FIXTURES = Path(__file__).parent / "config_lint_fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]

ISSUE_KEYS = {"rule", "severity", "message", "step", "location", "hint"}


def _fixture(name: str) -> str:
    return str(FIXTURES / name)


def test_clean_config_exits_zero(capsys):
    assert main([_fixture("clean.yaml")]) == 0
    assert "0 issue(s)" in capsys.readouterr().out


def test_errors_exit_one_and_are_reported(capsys):
    assert main([_fixture("c3_unknown_tool.yaml")]) == 1
    assert "C3-unknown-tool" in capsys.readouterr().out


def test_warnings_alone_exit_zero_unless_strict(capsys):
    assert main([_fixture("c6_unreferenced.yaml")]) == 0
    assert main(["--strict", _fixture("c6_unreferenced.yaml")]) == 1
    assert "C6-unreferenced-entry] error" in capsys.readouterr().out


def test_missing_file_exits_two(capsys):
    assert main([_fixture("does_not_exist.yaml")]) == 2
    assert "lint-config" in capsys.readouterr().err


def test_malformed_yaml_exits_two(capsys):
    assert main([_fixture("invalid/malformed.yaml")]) == 2
    assert "lint-config" in capsys.readouterr().err


def test_non_mapping_config_exits_two(capsys):
    assert main([_fixture("invalid/not_a_mapping.yaml")]) == 2
    assert "lint-config" in capsys.readouterr().err


def test_directory_argument_expands_to_config_files(capsys):
    assert main([_fixture("invalid")]) == 2
    assert main([str(FIXTURES)]) == 1
    assert "C3-unknown-tool" in capsys.readouterr().out


def test_no_argument_lints_the_shipped_config(capsys):
    assert Path(default_target()).is_file()
    assert main([]) == 0
    assert "issue(s)" in capsys.readouterr().out


def test_json_format_is_serialized_issues(capsys):
    assert main(["--format", "json", _fixture("c2_dangling_ref.yaml")]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["rule"] == "C2-dangling-ref"
    assert set(payload[0]) == ISSUE_KEYS


def test_json_output_is_pure_with_no_contamination(capsys):
    assert main(["--format", "json", _fixture("clean.yaml")]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == []


def test_run_loop_main_dispatches_the_lint_config_subcommand():
    assert run_loop_main(["lint-config", _fixture("clean.yaml")]) == 0
    assert run_loop_main(["lint-config", _fixture("c3_unknown_tool.yaml")]) == 1


def test_module_entrypoint_end_to_end():
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nebuko.engine",
            "lint-config",
            "--strict",
            _fixture("c6_unreferenced.yaml"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, result.stderr
    assert "C6-unreferenced-entry" in result.stdout
