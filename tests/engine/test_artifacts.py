"""Spec-scenario tests for per-run log staging (issue #743).

The new-engine host runs the legacy agent in a throwaway temp directory, so the
run's ``console.log`` and the OpenHands SDK's on-disk ``app.log`` must be staged
in a host-owned directory the workflow can upload as the ``nebuko-task-logs``
artifact.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from engine.artifacts import CONSOLE_LOG_FILE, RunArtifactStager, RunArtifacts


class TestRunArtifactStager:
    def test_log_dir_is_absolute_even_for_a_relative_base(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        stager = RunArtifactStager("runs/run-1")

        assert stager.log_dir().is_absolute()
        assert stager.log_dir() == (tmp_path / "runs" / "run-1").resolve()

    def test_child_log_env_enables_the_file_log_in_the_staging_dir(self, tmp_path):
        staging = tmp_path / "nebuko-task-logs"

        env = RunArtifactStager(staging).child_log_env()

        assert env == {"LOG_TO_FILE": "true", "LOG_DIR": str(staging.resolve())}

    def test_stage_console_log_creates_the_dir_and_writes_the_output(self, tmp_path):
        staging = tmp_path / "nested" / "nebuko-task-logs"

        RunArtifactStager(staging).stage_console_log("[phase:normalize] ok\n")

        assert (staging / CONSOLE_LOG_FILE).read_text(encoding="utf-8") == "[phase:normalize] ok\n"

    def test_bundle_freezes_the_staging_dir_and_the_output(self, tmp_path):
        staging = tmp_path / "nebuko-task-logs"

        artifacts = RunArtifactStager(staging).bundle("console output")

        assert isinstance(artifacts, RunArtifacts)
        assert artifacts.staging_dir == staging.resolve()
        assert artifacts.console_log == "console output"
        assert (staging / CONSOLE_LOG_FILE).read_text(encoding="utf-8") == "console output"


class TestRunArtifacts:
    def test_is_immutable(self, tmp_path):
        artifacts = RunArtifacts(staging_dir=tmp_path, console_log="x")

        with pytest.raises(FrozenInstanceError):
            artifacts.console_log = "y"  # type: ignore[misc]
