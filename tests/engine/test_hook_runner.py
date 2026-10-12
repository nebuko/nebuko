"""Unit tests for the session hook runner (issue #884)."""

import json
from pathlib import Path

import pytest

from config import HookSpec
from nebuko.engine import hook_runner
from nebuko.engine.hook_runner import (
    STEP_HOOKS_KEY,
    HookPayloadError,
    HookResult,
    HookRunner,
    _timeout_seconds,
    hooks_from_resolved,
)


def _write_script(root: Path, rel: str, body: str) -> Path:
    """Write an executable ``/bin/sh`` script at ``root/rel``."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _runner(workspace: Path, env: dict | None = None) -> HookRunner:
    return HookRunner(workspace=workspace, env=env or {})


class TestHookRunner:
    def test_success_returns_ok_with_output_tail(self, tmp_path):
        _write_script(tmp_path, "scripts/ok.sh", 'echo "hello from hook"')
        spec = HookSpec(path="scripts/ok.sh")

        result = _runner(tmp_path).run(spec, "pre")

        assert isinstance(result, HookResult)
        assert result.phase == "pre"
        assert result.ok is True
        assert result.exit_code == 0
        assert "hello from hook" in result.output_tail
        assert result.detail == "pre_hook exited with code 0"

    def test_non_zero_exit_is_data_not_an_exception(self, tmp_path):
        _write_script(tmp_path, "scripts/fail.sh", "exit 3")
        spec = HookSpec(path="scripts/fail.sh")

        result = _runner(tmp_path).run(spec, "post")

        assert result.ok is False
        assert result.exit_code == 3
        assert result.detail == "post_hook exited with code 3"

    def test_timeout_is_data_not_an_exception(self, tmp_path, monkeypatch):
        # Keep the wall clock short: the minute granularity is exercised by
        # ``_timeout_seconds`` separately.
        monkeypatch.setattr(hook_runner, "_timeout_seconds", lambda _minutes: 1)
        _write_script(tmp_path, "scripts/slow.sh", "sleep 5")
        spec = HookSpec(path="scripts/slow.sh", timeout_minutes=1)

        result = _runner(tmp_path).run(spec, "pre")

        assert result.ok is False
        assert result.exit_code is None
        assert "timed out" in result.detail

    def test_missing_script_reports_could_not_start(self, tmp_path):
        spec = HookSpec(path="scripts/absent.sh")

        result = _runner(tmp_path).run(spec, "pre")

        assert result.ok is False
        assert result.exit_code is None
        assert "could not start" in result.detail

    def test_runs_in_the_session_workspace(self, tmp_path):
        # A relative write from the hook lands in the workspace, proving cwd.
        _write_script(tmp_path, "scripts/touch.sh", "touch made.txt")
        spec = HookSpec(path="scripts/touch.sh")

        result = _runner(tmp_path).run(spec, "pre")

        assert result.ok is True
        assert (tmp_path / "made.txt").exists()

    def test_receives_the_session_env(self, tmp_path):
        _write_script(tmp_path, "scripts/env.sh", 'echo "token=$HOOK_TOKEN"')
        spec = HookSpec(path="scripts/env.sh")

        result = _runner(tmp_path, env={"HOOK_TOKEN": "abc"}).run(spec, "pre")

        assert result.ok is True
        assert "token=abc" in result.output_tail

    def test_args_are_passed_as_argv(self, tmp_path):
        _write_script(tmp_path, "scripts/args.sh", 'echo "args=$1,$2"')
        spec = HookSpec(path="scripts/args.sh", args=["one", "two"])

        result = _runner(tmp_path).run(spec, "post")

        assert result.ok is True
        assert "args=one,two" in result.output_tail

    def test_output_tail_is_truncated_to_the_cap(self, tmp_path):
        _write_script(tmp_path, "scripts/big.sh", "printf 'x%.0s' $(seq 1 5000)")
        spec = HookSpec(path="scripts/big.sh")

        result = _runner(tmp_path).run(spec, "post")

        assert len(result.output_tail) == 2000


class TestTimeoutSeconds:
    def test_defaults_to_thirty_minutes(self):
        assert _timeout_seconds(None) == 30 * 60

    def test_uses_the_declared_minutes(self):
        assert _timeout_seconds(5) == 5 * 60

    def test_caps_at_sixty_minutes(self):
        assert _timeout_seconds(120) == 60 * 60


class TestHooksFromResolved:
    def test_no_payload_decodes_to_no_hooks(self):
        assert hooks_from_resolved({}) == {}
        assert hooks_from_resolved({"STEP_HOOKS": ""}) == {}
        assert hooks_from_resolved(None) == {}

    def test_decodes_each_phase_to_a_hook_spec(self):
        raw = json.dumps(
            {
                "pre": {"path": "scripts/init.sh", "args": ["--full"], "timeout_minutes": 5},
                "post": {"path": "scripts/stats.sh", "args": None, "timeout_minutes": None},
            }
        )

        hooks = hooks_from_resolved({STEP_HOOKS_KEY: raw})

        assert set(hooks) == {"pre", "post"}
        assert hooks["pre"] == HookSpec(
            path="scripts/init.sh", args=["--full"], timeout_minutes=5
        )
        assert hooks["post"].path == "scripts/stats.sh"

    def test_decodes_a_single_phase(self):
        raw = json.dumps({"post": {"path": "scripts/stats.sh"}})

        hooks = hooks_from_resolved({STEP_HOOKS_KEY: raw})

        assert set(hooks) == {"post"}

    def test_malformed_json_raises(self):
        with pytest.raises(HookPayloadError, match="invalid STEP_HOOKS"):
            hooks_from_resolved({STEP_HOOKS_KEY: "{not json"})

    def test_non_object_payload_raises(self):
        with pytest.raises(HookPayloadError, match="must decode to an object"):
            hooks_from_resolved({STEP_HOOKS_KEY: "[]"})

    def test_invalid_hook_spec_raises(self):
        raw = json.dumps({"pre": {"path": "/etc/passwd"}})
        with pytest.raises(HookPayloadError, match="invalid pre_hook"):
            hooks_from_resolved({STEP_HOOKS_KEY: raw})
