"""Structural guard for the workflow-pinned workspace checkout (issue #862).

A workflow pinned via ``uses: <repo>/…@<sha1|tag>`` used to run against a
``main`` checkout: the ``run-task`` ``Checkout repository`` step was bare, so
``actions/checkout`` fell back to the event default. These tests pin the YAML
contract that fixes it — the ``checkout_ref`` input, the guarded
``job.workflow_sha`` expression on ``run-task``, the untouched
``agent-metrics`` self-checkout, and the ``NEBUKO_WORKFLOW_REF`` env wiring.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))


def _steps(job: str) -> list[dict]:
    return _workflow()["jobs"][job]["steps"]


def _checkout_step(job: str, name: str) -> dict:
    return next(s for s in _steps(job) if s.get("name") == name)


class TestCheckoutRefInput:
    def test_declared_with_empty_default(self):
        inputs = _workflow()[True]["workflow_call"]["inputs"]
        assert "checkout_ref" in inputs
        assert inputs["checkout_ref"]["default"] == ""
        assert inputs["checkout_ref"]["type"] == "string"


class TestRunTaskCheckoutPin:
    def _ref(self) -> str:
        return _checkout_step("run-task", "Checkout repository")["with"]["ref"]

    def test_explicit_override_wins(self):
        assert "inputs.checkout_ref" in self._ref()

    def test_auto_pins_to_the_workflow_commit(self):
        assert "job.workflow_sha" in self._ref()

    def test_guarded_by_the_same_repo_comparison(self):
        # Without the guard a cross-repo caller would checkout a SHA that does
        # not exist in their repo. The comparison is load-bearing.
        assert "job.workflow_repository == github.repository" in self._ref()

    def test_credentials_not_persisted(self):
        step = _checkout_step("run-task", "Checkout repository")
        assert step["with"]["persist-credentials"] is False


class TestAgentMetricsSelfCheckoutUnchanged:
    def _step(self) -> dict:
        return _checkout_step("agent-metrics", "Checkout nebuko repo (reusable workflow mode)")

    def test_still_pins_to_the_workflow_repo_and_sha(self):
        step = self._step()
        assert step["with"]["repository"] == "${{ job.workflow_repository }}"
        assert step["with"]["ref"] == "${{ job.workflow_sha }}"


class TestWorkflowRefEnv:
    def test_engine_step_forwards_job_workflow_ref(self):
        engine = next(
            s for s in _steps("run-task")
            if s.get("name") == "Run orchestration engine"
        )
        assert engine["env"]["NEBUKO_WORKFLOW_REF"] == "${{ job.workflow_ref }}"
