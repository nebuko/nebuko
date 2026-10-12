"""Spec-scenario tests for per-run log staging (issues #743, #779, #894).

The new-engine host runs the legacy agent in a throwaway temp directory, so the
run's ``console.txt`` and the OpenHands SDK's on-disk ``app.log`` must be staged
in a host-owned directory the workflow uploads — each file as its own *unzipped*
artifact (issue #779) so a run's logs can be fetched raw, without any unzip. The
SDK's ``app.log`` is renamed to the browser-viewable ``app.txt`` (issue #894).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from nebuko.adapters.github import ARTIFACT_DIR_NAME
from nebuko.engine.artifacts import (
    APP_LOG_FILE,
    CONSOLE_LOG_FILE,
    SDK_APP_LOG_FILE,
    ArtifactFile,
    RunArtifactStager,
    RunArtifacts,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"


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

    def test_artifact_files_lists_the_per_file_upload_manifest(self, tmp_path):
        staging = tmp_path / "nebuko-task-logs"

        files = RunArtifactStager(staging).artifact_files()

        assert files == (
            ArtifactFile(name=CONSOLE_LOG_FILE, path=staging.resolve() / CONSOLE_LOG_FILE),
            ArtifactFile(name=APP_LOG_FILE, path=staging.resolve() / APP_LOG_FILE),
        )
        assert all(artifact.path.is_absolute() for artifact in files)

    def test_bundle_populates_the_per_file_manifest(self, tmp_path):
        staging = tmp_path / "nebuko-task-logs"

        artifacts = RunArtifactStager(staging).bundle("console output")

        assert artifacts.files == RunArtifactStager(staging).artifact_files()

    def test_manifest_names_use_the_browser_viewable_txt_extension(self, tmp_path):
        staging = tmp_path / "nebuko-task-logs"

        names = [artifact.name for artifact in RunArtifactStager(staging).artifact_files()]

        assert names == ["console.txt", "app.txt"]

    def test_bundle_renames_the_sdk_app_log_to_app_txt(self, tmp_path):
        staging = tmp_path / "nebuko-task-logs"
        staging.mkdir()
        (staging / SDK_APP_LOG_FILE).write_text("sdk log line\n", encoding="utf-8")

        RunArtifactStager(staging).bundle("console output")

        assert not (staging / SDK_APP_LOG_FILE).exists()
        assert (staging / APP_LOG_FILE).read_text(encoding="utf-8") == "sdk log line\n"
        assert RunArtifactStager(staging).artifact_files()[-1].path.is_file()

    def test_bundle_without_an_sdk_log_still_freezes_the_manifest(self, tmp_path):
        staging = tmp_path / "nebuko-task-logs"

        artifacts = RunArtifactStager(staging).bundle("console output")

        assert not (staging / APP_LOG_FILE).exists()
        assert [artifact.name for artifact in artifacts.files] == ["console.txt", "app.txt"]


class TestArtifactFile:
    def test_is_immutable(self, tmp_path):
        artifact = ArtifactFile(name=CONSOLE_LOG_FILE, path=tmp_path / CONSOLE_LOG_FILE)

        with pytest.raises(FrozenInstanceError):
            artifact.name = APP_LOG_FILE  # type: ignore[misc]

    def test_is_hashable_by_value(self):
        artifact = ArtifactFile(name=CONSOLE_LOG_FILE, path=Path("/tmp/console.txt"))

        assert artifact == ArtifactFile(name=CONSOLE_LOG_FILE, path=Path("/tmp/console.txt"))


class TestWorkflowUploadContract:
    """Every upload step stays in lock-step with the manifest (issue #779).

    The engine (staging), the workflow YAML (upload) and the debug prompt
    (download) share one contract; this pins the YAML half so the upload steps
    cannot drift from ``RunArtifactStager.artifact_files``.
    """

    def _upload_steps(self) -> list[dict]:
        workflow = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
        steps: list[dict] = []

        def _walk(node: object) -> None:
            if isinstance(node, dict):
                uses = node.get("uses")
                if isinstance(uses, str) and uses.startswith("actions/upload-artifact"):
                    steps.append(node)
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(workflow)
        return steps

    def test_each_upload_is_unzipped_and_matches_the_manifest(self):
        manifest = {f.name for f in RunArtifactStager(Path(ARTIFACT_DIR_NAME)).artifact_files()}
        steps = self._upload_steps()

        assert steps, "expected at least one actions/upload-artifact step"
        uploaded: set[str] = set()
        for step in steps:
            assert step["with"]["archive"] is False
            assert step.get("if") == "always()"
            uploaded.add(Path(step["with"]["path"]).name)
        assert uploaded == manifest

    def test_steps_upload_from_the_adapter_staging_dir(self):
        for step in self._upload_steps():
            assert f"/{ARTIFACT_DIR_NAME}/" in step["with"]["path"]


class TestRunArtifacts:
    def test_is_immutable(self, tmp_path):
        artifacts = RunArtifacts(staging_dir=tmp_path, console_log="x")

        with pytest.raises(FrozenInstanceError):
            artifacts.console_log = "y"  # type: ignore[misc]
