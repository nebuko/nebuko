"""Contract: the reusable workflow checks the personal repo out for Lane D (issues #1479, #1483).

The personal lane reads its manifest and agent overrides from ``.nebuko-personal``.
Nothing in the workflow checks that repo out yet, so this test is an expected
failure until a human applies the step (issue 1483 tracks the workflow patch). It
turns into an unexpected pass once the step lands; the xfail is then removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"


def _steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


@pytest.mark.xfail(
    strict=False,
    reason=(
        "issue 1483: the reusable workflow does not check out the personal repo into "
        ".nebuko-personal yet; a human applies the step described in docs/claude-code.md"
    ),
)
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
    assert "nebuko-personal" in str(with_.get("repository", ""))
    # Default branch: no explicit ref may pin the personal checkout.
    assert not with_.get("ref")


def test_project_checkout_stays_separate_from_the_personal_checkout():
    # The personal checkout must never replace the project workspace.
    paths = [(step.get("with") or {}).get("path") for step in _steps()]
    assert paths.count(".nebuko-personal") <= 1
