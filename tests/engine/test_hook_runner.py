"""Tests for the step-hook executor (issue #884)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from config import HookSpec
from nebuko.engine import hook_runner
from nebuko.engine.hook_runner import HookResult, HookRunner


def _script(workspace: Path, name: str, body: str) -> str:
    """Write an executable repo-relative script and return its relative path."""
    path = workspace / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return name


def _runner(workspace: Path) -> HookRunner:
    return HookRunner(workspace=workspace, env={**os.environ})


class TestHookRunnerSuccess:
    def test_running_hook_returns_success(self, tmp_path):
        rel = _script(tmp_path, "scripts/ok.sh", "echo hello\n")
        result = _runner(tmp_path).run(HookSpec(path=rel), "pre")

        assert result == HookResult(
            phase="pre",
            ok=True,
            exit_code=0,
            detail=f"pre_hook '{rel}' completed (exit 0)",
            output_tail="hello\n",
        )

    def test_hook_runs_in_the_workspace(self, tmp_path):
        rel = _script(tmp_path, "scripts/pwd.sh", "pwd\n")
        result = _runner(tmp_path).run(HookSpec(path=rel), "post")

        assert result.ok
        assert result.output_tail.strip() == str(tmp_path)

    def test_args_are_forwarded(self, tmp_path):
        rel = _script(tmp_path, "scripts/args.sh", 'echo "$1-$2"\n')
        runner = _runner(tmp_path)

        result = runner.run(HookSpec(path=rel, args=["a", "b"]), "pre")

        assert result.output_tail == "a-b\n"

    def test_env_is_forwarded(self, tmp_path):
        rel = _script(tmp_path, "scripts/env.sh", 'echo "$HOOK_VAR"\n')
        runner = HookRunner(workspace=tmp_path, env={"HOOK_VAR": "hello", "PATH": os.environ["PATH"]})

        result = runner.run(HookSpec(path=rel), "pre")

        assert result.output_tail == "hello\n"

    def test_long_output_is_truncated_to_the_tail(self, tmp_path):
        rel = _script(
            tmp_path,
            "scripts/long.sh",
            "printf 'START'; i=0; while [ $i -lt 3000 ]; do printf 'x'; i=$((i+1)); done; printf 'END'\n",
        )

        result = _runner(tmp_path).run(HookSpec(path=rel), "pre")

        assert result.ok
        assert result.output_tail.endswith("END")
        assert len(result.output_tail) <= hook_runner._OUTPUT_TAIL_CHARS
        assert not result.output_tail.startswith("START")


class TestHookRunnerFailure:
    def test_non_zero_exit_is_reported(self, tmp_path):
        rel = _script(tmp_path, "scripts/fail.sh", "echo boom\nexit 3\n")
        result = _runner(tmp_path).run(HookSpec(path=rel), "pre")

        assert result.ok is False
        assert result.exit_code == 3
        assert "exited with code 3" in result.detail
        assert result.output_tail == "boom\n"

    def test_missing_script_never_raises(self, tmp_path):
        result = _runner(tmp_path).run(HookSpec(path="scripts/missing.sh"), "pre")

        assert result.ok is False
        assert result.exit_code is None
        assert "could not be executed" in result.detail

    def test_timeout_is_reported_as_data(self, tmp_path, monkeypatch):
        # A validated ``timeout_minutes`` is >= 1, so the real 60s bound is
        # unworkable in a unit test; shrink the pure helper to 1s and exercise
        # the real ``subprocess.run`` timeout path.
        monkeypatch.setattr(hook_runner, "_timeout_seconds", lambda spec: 1)
        rel = _script(tmp_path, "scripts/slow.sh", "sleep 30\n")
        started = time.monotonic()

        result = _runner(tmp_path).run(HookSpec(path=rel), "post")

        assert result.ok is False
        assert result.exit_code is None
        assert result.phase == "post"
        assert "timed out" in result.detail
        assert time.monotonic() - started < 15
