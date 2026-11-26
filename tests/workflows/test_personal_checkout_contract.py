"""Contract: the reusable workflow checks the personal repo out for Lane D (issues #1479, #1483).

The personal lane reads its manifest and agent overrides from ``.nebuko-personal``.
The step lives in the reusable workflow, which the engine cannot push; it was
applied by a human (issue 1483 tracks the workflow patch).
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"


def _steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _job_steps_running_the_engine() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        steps = job.get("steps", [])
        if any(step.get("name") == "Run orchestration engine" for step in steps):
            return steps
    raise AssertionError("no job runs the orchestration engine")


def test_reusable_workflow_checks_out_the_personal_repo_read_only():
    checkouts = [
        step
        for step in _steps()
        if str(step.get("uses", "")).startswith("actions/checkout")
        and (step.get("with") or {}).get("path") == ".nebuko-personal"
    ]
    assert checkouts, "no checkout step writes the personal repo to .nebuko-personal"
    with_ = checkouts[0]["with"]
    assert with_.get("persist-credentials") is False
    # Only the .nebuko tree is read: the engine needs the agent configs and the manifest.
    assert with_.get("sparse-checkout") == ".nebuko"
    # No token override: the caller job's github.token reads the run's own repo.
    assert "token" not in with_
    # Default branch: no explicit ref may pin the personal checkout.
    assert not with_.get("ref")
    # The guard names the personal repo: the step runs only in a personal-repo run.
    assert "nebuko-personal" in str(checkouts[0].get("if", ""))
    assert "github.repository" in str(checkouts[0].get("if", ""))

    # Position: after the project checkout (which cleans the workspace root), before the engine.
    steps = _job_steps_running_the_engine()
    names = [step.get("name", "") for step in steps]
    personal = steps.index(checkouts[0])
    project = names.index("Checkout repository")
    engine = names.index("Run orchestration engine")
    assert project < personal < engine


def test_project_checkout_stays_separate_from_the_personal_checkout():
    # The personal checkout must never replace the project workspace.
    paths = [(step.get("with") or {}).get("path") for step in _steps()]
    assert paths.count(".nebuko-personal") <= 1
