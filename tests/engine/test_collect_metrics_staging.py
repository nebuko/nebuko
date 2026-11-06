"""``save_metrics_json`` stages a copy of the metrics for the run artifact.

The agent runs in a throwaway cwd, so the relative ``output/`` copy is deleted
with it. With ``LOG_DIR`` set (the host's staging dir) a second copy lands under
``$LOG_DIR/metrics/`` where ``RunArtifactStager`` bundles it into
``metrics.jsonl``.
"""

from __future__ import annotations

import json

from nebuko.engine.artifacts import METRICS_DIR_NAME, METRICS_LOG_FILE, RunArtifactStager
from nebuko.engine.lib.agent_script.features.collect_metrics.collect_metrics import (
    STAGED_METRICS_DIR,
    save_metrics_json,
)
from nebuko.engine.lib.metrics_schema import Metrics


def test_staged_dir_name_matches_the_stagers_bundle_source():
    assert STAGED_METRICS_DIR == METRICS_DIR_NAME


def test_stages_a_copy_under_log_dir(tmp_path, monkeypatch):
    work, logs = tmp_path / "work", tmp_path / "logs"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("LOG_DIR", str(logs))

    save_metrics_json(Metrics(run_id="abc", agent_runtimes_per_call_ms=[1200.0, 3400.0]))

    staged = json.loads((logs / METRICS_DIR_NAME / "metrics-abc.json").read_text())
    assert staged["run_id"] == "abc"
    assert staged["agent_runtimes_per_call_ms"] == [1200.0, 3400.0]
    assert (work / "output" / "metrics-abc.json").is_file()


def test_without_log_dir_only_the_output_copy_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOG_DIR", raising=False)

    save_metrics_json(Metrics(run_id="abc"))

    assert (tmp_path / "output" / "metrics-abc.json").is_file()
    assert not (tmp_path / METRICS_DIR_NAME).exists()


def test_an_unwritable_log_dir_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    # Under a regular file: not creatable even as root.
    monkeypatch.setenv("LOG_DIR", str(blocker / "logs"))

    save_metrics_json(Metrics(run_id="abc"))

    assert (tmp_path / "output" / "metrics-abc.json").is_file()


def test_staged_metrics_end_up_in_the_uploaded_bundle(tmp_path, monkeypatch):
    work, logs = tmp_path / "work", tmp_path / "logs"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("LOG_DIR", str(logs))

    save_metrics_json(Metrics(run_id="abc", model_name="m"))
    RunArtifactStager(logs).bundle("console")

    lines = (logs / METRICS_LOG_FILE).read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["run_id"] == "abc"
