"""Executor tests for the deterministic ``script`` step (issue #541).

Real subprocess execution against tiny fixture scripts (no pipeline mocks).
Covers the plan's test matrix: pass, fail, timeout, path safety, state
emission, and comment overflow splitting.
"""

import json
import os
import stat
import tempfile

import pytest

from features.run_script import run_script as rs


# ── Fixture scripts ──────────────────────────────────────────────


def _write_script(root: str, rel: str, body: str, executable: bool = True) -> str:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture()
def sandbox(tmp_path):
    """A checkout-like dir with fixture scripts; cwd is switched to it."""
    root = tmp_path
    _write_script(root, "scripts/pass.sh", "#!/bin/sh\necho hello-pass\n")
    _write_script(root, "scripts/fail.sh", "#!/bin/sh\necho about-to-fail\nexit 3\n")
    _write_script(root, "scripts/slow.sh", "#!/bin/sh\nsleep 5\n")
    old = os.getcwd()
    os.chdir(root)
    try:
        yield root
    finally:
        os.chdir(old)
        for name in ("final_message.txt", "workflow_state.json",
                     "final_message_parts_count.txt"):
            p = os.path.join(root, name)
            if os.path.exists(p):
                os.remove(p)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── Unit: config & safety ────────────────────────────────────────


class TestReadConfig:
    def test_requires_path(self):
        with pytest.raises(ValueError):
            rs.read_config({"SCRIPT_PATH": ""})

    def test_defaults_and_clamp(self):
        cfg = rs.read_config({"SCRIPT_PATH": "x.sh"})
        assert cfg["timeout_minutes"] == rs.DEFAULT_TIMEOUT_MINUTES
        cfg = rs.read_config({"SCRIPT_PATH": "x.sh", "SCRIPT_ARGS": "-a -b",
                              "SCRIPT_TIMEOUT_MINUTES": "900"})
        assert cfg["args"] == ["-a", "-b"]
        assert cfg["timeout_minutes"] == rs.MAX_TIMEOUT_MINUTES

    def test_rejects_bad_timeout(self):
        with pytest.raises(ValueError):
            rs.read_config({"SCRIPT_PATH": "x.sh", "SCRIPT_TIMEOUT_MINUTES": "abc"})
        with pytest.raises(ValueError):
            rs.read_config({"SCRIPT_PATH": "x.sh", "SCRIPT_TIMEOUT_MINUTES": "0"})


class TestValidatePath:
    def test_rejects_absolute(self):
        with pytest.raises(ValueError):
            rs.validate_script_path("/etc/passwd", "/tmp/whatever")

    def test_rejects_traversal(self):
        with pytest.raises(ValueError):
            rs.validate_script_path("../../etc/passwd", "/tmp/whatever")

    def test_rejects_missing(self):
        with pytest.raises(ValueError):
            rs.validate_script_path("nope.sh", "/tmp/whatever")

    def test_accepts_nested(self, sandbox):
        resolved = rs.validate_script_path("scripts/pass.sh", sandbox)
        assert resolved == os.path.realpath(os.path.join(sandbox, "scripts/pass.sh"))

    def test_accepts_dotdot_prefixed_filename(self, sandbox):
        # '..bar' is a literal filename, not a traversal segment.
        _write_script(sandbox, "scripts/..bar.sh", "#!/bin/sh\necho ok\n")
        rs.validate_script_path("scripts/..bar.sh", sandbox)


# ── Integration: full run() over temp fixtures ───────────────────


class TestRun:
    def _env(self, **kw):
        base = {
            "SCRIPT_PATH": "scripts/pass.sh",
            "SCRIPT_ARGS": "",
            "SCRIPT_TIMEOUT_MINUTES": "1",
            "WORKFLOW_NAME": "default",
            "WORKFLOW_STEP": "run-tests",
            "TRIGGER_BODY": "",
        }
        base.update(kw)
        return base

    def test_pass(self, sandbox, monkeypatch):
        monkeypatch.setenv("SCRIPT_PATH", "scripts/pass.sh")
        monkeypatch.setenv("SCRIPT_ARGS", "")
        monkeypatch.setenv("SCRIPT_TIMEOUT_MINUTES", "1")
        monkeypatch.setenv("WORKFLOW_NAME", "default")
        monkeypatch.setenv("WORKFLOW_STEP", "run-tests")
        monkeypatch.setenv("TRIGGER_BODY", "")
        assert rs.run() == 0
        comment = _read("final_message.txt")
        assert "PASS ✅" in comment
        assert "hello-pass" in comment
        assert "Exit code: 0" in comment
        state = json.load(open("workflow_state.json"))
        assert state["dispatch"]["branch"] == "pass"

    def test_fail(self, sandbox, monkeypatch):
        monkeypatch.setenv("SCRIPT_PATH", "scripts/fail.sh")
        monkeypatch.setenv("SCRIPT_ARGS", "")
        monkeypatch.setenv("SCRIPT_TIMEOUT_MINUTES", "1")
        monkeypatch.setenv("WORKFLOW_NAME", "default")
        monkeypatch.setenv("WORKFLOW_STEP", "run-tests")
        monkeypatch.setenv("TRIGGER_BODY", "")
        assert rs.run() == 0  # failure result is not a crash
        comment = _read("final_message.txt")
        assert "FAIL ⚠️" in comment
        assert "about-to-fail" in comment
        assert "Exit code: 3" in comment
        state = json.load(open("workflow_state.json"))
        assert state["dispatch"]["branch"] == "fail"

    def test_timeout(self, sandbox, monkeypatch):
        monkeypatch.setenv("SCRIPT_PATH", "scripts/slow.sh")
        monkeypatch.setenv("SCRIPT_ARGS", "")
        monkeypatch.setenv("SCRIPT_TIMEOUT_MINUTES", "1")
        monkeypatch.setenv("WORKFLOW_NAME", "default")
        monkeypatch.setenv("WORKFLOW_STEP", "run-tests")
        monkeypatch.setenv("TRIGGER_BODY", "")
        # Force a sub-second timeout to keep the test fast.
        orig = rs.execute_script
        monkeypatch.setattr(
            rs, "execute_script",
            lambda *a, **k: orig(a[0], a[1], 1, a[3]),
        )
        assert rs.run() == 0
        comment = _read("final_message.txt")
        assert "FAIL ⚠️" in comment
        assert "TIMED OUT" in comment
        state = json.load(open("workflow_state.json"))
        assert state["dispatch"]["branch"] == "fail"

    def test_missing_script_fails_fast(self, sandbox, monkeypatch):
        monkeypatch.setenv("SCRIPT_PATH", "does-not-exist.sh")
        assert rs.run() == 1
        assert not os.path.exists("final_message.txt")
        assert not os.path.exists("workflow_state.json")

    def test_absolute_path_rejected(self, sandbox, monkeypatch):
        monkeypatch.setenv("SCRIPT_PATH", "/etc/passwd")
        assert rs.run() == 1
        assert not os.path.exists("workflow_state.json")

    def test_overflow_splits(self, sandbox, monkeypatch):
        # A script emitting > 10k chars must produce split part files.
        _write_script(sandbox, "scripts/big.sh",
                      "#!/bin/sh\npython3 -c \"print('x' * 20000)\"\n")
        monkeypatch.setenv("SCRIPT_PATH", "scripts/big.sh")
        monkeypatch.setenv("SCRIPT_ARGS", "")
        monkeypatch.setenv("SCRIPT_TIMEOUT_MINUTES", "2")
        monkeypatch.setenv("WORKFLOW_NAME", "default")
        monkeypatch.setenv("WORKFLOW_STEP", "run-tests")
        monkeypatch.setenv("TRIGGER_BODY", "")
        assert rs.run() == 0
        parts = int(_read("final_message_parts_count.txt"))
        assert parts >= 2
        assert os.path.exists("final_message_part_2.txt")
        # Part 1 must respect the 10k cap.
        assert len(_read("final_message.txt")) <= rs.FINAL_MESSAGE_MAX_LEN


# ── Unit: formatting ─────────────────────────────────────────────


class TestFormat:
    def _cfg(self):
        return {"step": "run-tests", "path": "scripts/x.sh",
                "args": ["-a"], "timeout_minutes": 5}

    def test_pass_banner(self):
        c = rs.format_comment(self._cfg(),
                              {"exit_code": 0, "stdout": "out", "stderr": "",
                               "timed_out": False, "duration_s": 1.0})
        assert "PASS ✅" in c
        assert "out" in c

    def test_fence_escapes_backticks(self):
        c = rs.format_comment(self._cfg(),
                              {"exit_code": 0, "stdout": "has ``` triple",
                               "stderr": "", "timed_out": False, "duration_s": 0.5})
        # Fence must be longer than the embedded run so the block stays intact.
        assert "````" in c

    def test_stderr_appended(self):
        c = rs.format_comment(self._cfg(),
                              {"exit_code": 1, "stdout": "so", "stderr": "se",
                               "timed_out": False, "duration_s": 0.2})
        assert "so" in c and "se" in c
        assert "FAIL ⚠️" in c