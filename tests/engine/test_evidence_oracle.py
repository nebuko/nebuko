"""The evidence oracle of the root-cause workflow (issue #1348).

``.nebuko/scripts/evidence.py`` judges an investigation's ``repro.sh`` per
delta, so a fix that removes only some of the reported differences reads
``partial`` and a broken repro never reads as fixed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".nebuko" / "scripts" / "evidence.py"

SYMPTOM = {"failed": 31, "collected": 4208, "subtests": 6}
HEALTHY = {"failed": 4, "collected": 4296, "subtests": 14}


def _evidence(root: Path, *, version: int = 1, state: dict | None = None) -> Path:
    """A v<k> dir whose repro.sh echoes ``state.json`` as its last line."""
    directory = root / "issue-9" / f"v{version}"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps(state or SYMPTOM))
    (directory / "repro.sh").write_text('echo "noise before the result"\ncat state.json\n')
    (directory / "expected.json").write_text(
        json.dumps(
            {
                "deltas": {
                    "failed": {"symptom": 31, "healthy": 4},
                    "collected": {"symptom": 4208, "healthy": 4296},
                    "subtests": {"symptom": 6},  # healthy: anything else
                }
            }
        )
    )
    return directory


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=full_env,
    )


def _report(proc: subprocess.CompletedProcess[str]) -> dict:
    body = proc.stdout.split("EVIDENCE_VERDICT=")[0]
    return json.loads(body)


class TestRun:
    def test_given_every_delta_shows_the_symptom_then_present_and_exit_0(self, tmp_path):
        directory = _evidence(tmp_path)
        proc = _run("run", str(directory))
        assert proc.returncode == 0
        assert _report(proc)["verdict"] == "present"
        assert "EVIDENCE_VERDICT=present" in proc.stdout

    def test_given_every_delta_healthy_then_absent_matches_expect_absent(self, tmp_path):
        directory = _evidence(tmp_path, state={"failed": 4, "collected": 4296, "subtests": 14})
        assert _run("run", str(directory), "--expect", "absent").returncode == 0
        assert _run("run", str(directory), "--expect", "present").returncode == 1

    def test_given_only_some_deltas_fixed_then_partial_with_exit_3(self, tmp_path):
        directory = _evidence(tmp_path, state={"failed": 4, "collected": 4208, "subtests": 14})
        proc = _run("run", str(directory), "--expect", "absent")
        report = _report(proc)
        assert proc.returncode == 3
        assert report["verdict"] == "partial"
        assert {n: d["status"] for n, d in report["deltas"].items()} == {
            "failed": "healthy", "collected": "symptom", "subtests": "healthy",
        }

    def test_given_a_value_that_is_neither_symptom_nor_healthy_then_inconclusive(self, tmp_path):
        directory = _evidence(tmp_path, state={"failed": 12, "collected": 4208, "subtests": 6})
        proc = _run("run", str(directory), "--expect", "absent")
        assert proc.returncode == 2
        assert _report(proc)["deltas"]["failed"]["status"] == "other"

    def test_given_a_missing_delta_then_inconclusive_never_fixed(self, tmp_path):
        directory = _evidence(tmp_path, state={"failed": 4, "collected": 4296})
        proc = _run("run", str(directory), "--expect", "absent")
        assert proc.returncode == 2
        assert _report(proc)["deltas"]["subtests"]["status"] == "missing"

    @pytest.mark.parametrize(
        "repro",
        ["exit 3", "echo not-json", "echo '[1, 2]'", ":"],
        ids=["exit-code", "garbage", "not-an-object", "silent"],
    )
    def test_given_a_broken_repro_then_inconclusive(self, tmp_path, repro):
        directory = _evidence(tmp_path)
        (directory / "repro.sh").write_text(repro + "\n")
        proc = _run("run", str(directory), "--expect", "absent")
        assert proc.returncode == 2
        assert _report(proc)["error"]

    def test_given_a_repro_that_hangs_then_inconclusive(self, tmp_path):
        directory = _evidence(tmp_path)
        (directory / "repro.sh").write_text("sleep 30\n")
        proc = _run("run", str(directory), "--timeout", "1")
        assert proc.returncode == 2
        assert "timed out" in _report(proc)["error"]

    def test_given_an_issue_dir_then_the_newest_version_is_run(self, tmp_path):
        _evidence(tmp_path, version=1)
        _evidence_v2 = tmp_path / "issue-9" / "v2"
        _evidence_v2.mkdir()
        (_evidence_v2 / "repro.sh").write_text('echo \'{"failed": 4}\'\n')
        (_evidence_v2 / "expected.json").write_text(
            json.dumps({"deltas": {"failed": {"symptom": 31, "healthy": 4}}})
        )
        proc = _run("run", str(tmp_path / "issue-9"), "--expect", "absent")
        assert proc.returncode == 0
        assert _report(proc)["dir"].endswith("v2")

    def test_given_issue_auto_then_the_event_payload_picks_the_dir(self, tmp_path):
        _evidence(tmp_path)
        event = tmp_path / "event.json"
        event.write_text(json.dumps({"issue": {"number": 9}}))
        proc = _run("run", "--issue", "auto", "--root", str(tmp_path),
                    env={"GITHUB_EVENT_PATH": str(event)})
        assert proc.returncode == 0

    @pytest.mark.parametrize(
        "expected",
        [{}, {"deltas": {}}, {"deltas": {"a": {}}}, {"deltas": {"a": {"symptom": 1, "healthy": 1}}}],
        ids=["empty", "no-deltas", "no-symptom", "symptom-equals-healthy"],
    )
    def test_given_an_unusable_expected_file_then_exit_2(self, tmp_path, expected):
        directory = _evidence(tmp_path)
        (directory / "expected.json").write_text(json.dumps(expected))
        assert _run("run", str(directory)).returncode == 2


class TestManifestAndVerify:
    def test_given_a_fresh_manifest_then_verify_passes(self, tmp_path):
        directory = _evidence(tmp_path)
        assert _run("manifest", str(directory)).returncode == 0
        assert _run("verify", str(directory)).returncode == 0

    @pytest.mark.parametrize("tamper", ["edit", "add", "remove"])
    def test_given_a_change_after_the_manifest_then_verify_fails(self, tmp_path, tamper):
        directory = _evidence(tmp_path)
        _run("manifest", str(directory))
        if tamper == "edit":
            (directory / "state.json").write_text(json.dumps(HEALTHY))
        elif tamper == "add":
            (directory / "extra.txt").write_text("x")
        else:
            (directory / "state.json").unlink()
        proc = _run("verify", str(directory))
        assert proc.returncode == 1
        assert "changed=" in proc.stderr

    def test_given_an_unusable_oracle_then_the_manifest_is_refused(self, tmp_path):
        directory = _evidence(tmp_path)
        (directory / "expected.json").write_text("{}")
        assert _run("manifest", str(directory)).returncode == 2
        assert not (directory / "manifest.json").exists()

    def test_given_a_committed_dir_then_verify_reports_and_checks_the_git_tree(self, tmp_path):
        directory = _evidence(tmp_path)
        _run("manifest", str(directory))
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run([*git, "-C", str(tmp_path), "add", "-A"], check=True)
        subprocess.run([*git, "-C", str(tmp_path), "commit", "-qm", "e"], check=True)
        first = _run("verify", str(directory))
        tree = next(ln for ln in first.stdout.splitlines() if ln.startswith("tree: "))[6:]
        assert _run("verify", str(directory), "--sealed-tree", tree).returncode == 0
        assert _run("verify", str(directory), "--sealed-tree", "0" * 40).returncode == 1
