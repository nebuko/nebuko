"""Contract tests for the nebuko reusable workflow's dependency fast path.

The workflow must install the agent runtime exactly once, from the locked
``uv.lock`` set (issue #982 pinning), run every ``uv run`` with ``--no-sync``
so no step re-resolves, and hold ``actions: write`` so setup-uv can persist
the uv cache across runs.

RED against the current workflow: the tests fail until the fast-path change
lands in ``.github/workflows/nebuko-workflow.yaml``.
"""

from __future__ import annotations

from typing import Any, ClassVar
from pathlib import Path

import pytest
import yaml


class WorkflowSpec:
    """Read-only accessor over the reusable workflow YAML for contract tests."""

    WORKFLOW_PATH: ClassVar[Path] = Path(".github/workflows/nebuko-workflow.yaml")

    data: dict[str, Any]

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, text: str) -> None:
        self.data = yaml.safe_load(text)

    @classmethod
    def load(cls) -> "WorkflowSpec":
        return cls(cls.WORKFLOW_PATH.read_text())

    # @stub-protected: signature defined by architect — do not modify
    def job(self, name: str) -> dict[str, Any]:
        jobs = self.data.get("jobs", {})
        if name not in jobs:
            raise KeyError(f"workflow job '{name}' not found")
        return jobs[name]

    # @stub-protected: signature defined by architect — do not modify
    def run_bodies(self, job_name: str) -> list[str]:
        steps = self.job(job_name).get("steps", [])
        return [step["run"] for step in steps if "run" in step]

    def permissions_of(self, job_name: str) -> set[str]:
        return set(self.job(job_name).get("permissions", {}))


def test_run_task_grants_cache_write() -> None:
    """Given the reusable workflow, when the run-task job is inspected,
    then it declares actions: write so the uv cache can be saved."""
    spec = WorkflowSpec.load()
    assert "actions" in spec.permissions_of("run-task")


def test_no_unpinned_sdk_install() -> None:
    """Given the run-task job's shell steps, when each run body is scanned,
    then none installs the SDK unpinned from @main (issue #982)."""
    spec = WorkflowSpec.load()
    bodies = spec.run_bodies("run-task")
    offenders = [b for b in bodies if "software-agent-sdk.git@main" in b]
    assert offenders == []


def test_exactly_one_locked_sync() -> None:
    """Given the run-task job's shell steps, when sync steps are counted,
    then exactly one body runs `uv sync`, locked and with the runtime extra."""
    spec = WorkflowSpec.load()
    bodies = spec.run_bodies("run-task")
    sync_bodies = [b for b in bodies if "uv sync" in b]
    assert len(sync_bodies) == 1
    assert "--locked" in sync_bodies[0]
    assert "--extra runtime" in sync_bodies[0]


def test_engine_steps_do_not_resync() -> None:
    """Given the secret-materialize and engine-run steps, when their run
    bodies are inspected, then both use --no-sync and no --extra runtime."""
    spec = WorkflowSpec.load()
    bodies = spec.run_bodies("run-task")
    run_bodies = [b for b in bodies if "python -m nebuko.engine" in b or "secret_env" in b]
    assert len(run_bodies) >= 2
    for body in run_bodies:
        assert "--no-sync" in body, f"step re-resolves: {body}"
        assert "--extra runtime" not in body, f"step re-resolves extra: {body}"
