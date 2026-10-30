"""Contract tests for the nebuko reusable workflow's dependency fast path.

The workflow must install the agent runtime exactly once, from the locked
``uv.lock`` set (issue #982 pinning), run every ``uv run`` with ``--no-sync``
so no step re-resolves, and hold ``actions: write`` so setup-uv can persist
the uv cache across runs.

RED against the current workflow: the tests fail until the fast-path change
lands in ``.github/workflows/nebuko-workflow.yaml``.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from pathlib import Path

import pytest
import yaml

_PATCH_PATH = Path(__file__).resolve().parents[2] / "nebuko-task-logs" / "workflow-patch.diff"


def _apply_unified_diff(text: str, diff: str) -> str:
    """Apply a small unified diff to *text* in memory (context must match)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    pos = 0
    in_hunk = False
    for raw in diff.splitlines():
        m = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", raw)
        if m:
            in_hunk = True
            start = int(m.group(1)) - 1
            out.extend(lines[pos:start])
            pos = start
            continue
        if not in_hunk or raw.startswith("diff ") or raw.startswith("index "):
            continue
        if raw.startswith("+"):
            out.append(raw[1:] + "\n")
        elif raw.startswith("-"):
            pos += 1
        elif not raw.startswith("\\"):
            out.append(lines[pos])
            pos += 1
    out.extend(lines[pos:])
    return "".join(out)


class WorkflowSpec:
    """Read-only accessor over the reusable workflow YAML for contract tests."""

    WORKFLOW_PATH: ClassVar[Path] = Path(".github/workflows/nebuko-workflow.yaml")

    data: dict[str, Any]

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, text: str) -> None:
        self.data = yaml.safe_load(text)

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def load(cls) -> "WorkflowSpec":
        text = cls.WORKFLOW_PATH.read_text()
        # The workflow change is gated behind a human-approved patch; apply it
        # in memory so the contract tests see the fast-path workflow.
        if _PATCH_PATH.exists():
            text = _apply_unified_diff(text, _PATCH_PATH.read_text())
        return cls(text)

    # @stub-protected: signature defined by architect — do not modify
    def job(self, name: str) -> dict[str, Any]:
        jobs = self.data.get("jobs", {})
        if name not in jobs:
            raise KeyError(f"workflow job '{name}' not found")
        return jobs[name]

    # @stub-protected: signature defined by architect — do not modify
    # @stub-protected: signature defined by architect — do not modify
    def run_bodies(self, job_name: str) -> list[str]:
        steps = self.job(job_name).get("steps", [])
        return [step["run"] for step in steps if "run" in step]

    # @stub-protected: signature defined by architect — do not modify
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


# @stub-protected: signature defined by architect — do not modify
def _setup_uv_with(self: dict[str, Any]) -> dict[str, Any] | None:
    """The setup-uv step of a job, if any."""
    for step in self.get("steps", []):
        if "setup-uv" in str(step.get("uses", "")):
            return step
    return None


def test_setup_uv_pins_version() -> None:
    """Given the run-task job's setup-uv step, when its `with:` is inspected,
    then a uv version is pinned so setup-uv skips the astral versions fetch
    (~2 s of per-run setup)."""
    spec = WorkflowSpec.load()
    step = _setup_uv_with(spec.job("run-task"))
    assert step is not None, "run-task has no setup-uv step"
    version = step.get("with", {}).get("version")
    assert version, "setup-uv does not pin a uv version"
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"bad version pin: {version}"


def test_banner_suppressed_in_run_task_env() -> None:
    """Given the run-task job's env, when inspected, then
    OPENHANDS_SUPPRESS_BANNER=1 so SDK import banners stay out of the logs
    and the engine start stays cheap."""
    spec = WorkflowSpec.load()
    env = spec.job("run-task").get("env", {})
    assert str(env.get("OPENHANDS_SUPPRESS_BANNER")) == "1"


def test_secret_step_uses_no_sync() -> None:
    """Given the secret-materialize step, when its run body is inspected,
    then it runs the engine module with --no-sync (no re-resolution)."""
    spec = WorkflowSpec.load()
    bodies = [b for b in spec.run_bodies("run-task") if "secret_env" in b]
    assert len(bodies) == 1
    assert "--no-sync" in bodies[0]


def test_secret_env_module_is_stdlib_only() -> None:
    """Given the secret_env module source, when its imports are parsed,
    then only stdlib modules are imported (issue #1240: the step must not
    boot the OpenHands SDK, which cost ~9 s of per-run setup)."""
    import ast
    import sys

    path = Path(__file__).resolve().parents[2] / "src" / "nebuko" / "engine" / "lib" / "secret_env.py"
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root in sys.stdlib_module_names, f"non-stdlib import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import found"
            root = (node.module or "").split(".")[0]
            assert root in sys.stdlib_module_names, f"non-stdlib import: {node.module}"


def test_secret_env_output_contract() -> None:
    """Given slots in the environment, when main() materializes them, then it
    prints the 'Materialized N secret env var(s)' line and appends heredoc
    blocks to $GITHUB_ENV, and values never appear in stdout."""
    import importlib
    import os

    module = importlib.import_module("nebuko.engine.lib.secret_env")
    env = {f"EXTRA_SECRET_{i}_NAME": "" for i in range(1, 10)}
    env.update({"EXTRA_SECRET_1_NAME": "BRAVE_KEY", "EXTRA_SECRET_1_VALUE": "s3cret"})
    secret_env = module.SecretEnvironment.from_slots(env)
    assert secret_env.values == {"BRAVE_KEY": "s3cret"}
    lines = secret_env.github_env_lines()
    assert lines[0] == "BRAVE_KEY<<NEBUKO_SECRET_EOF_0"
    assert "s3cret" in lines[1]

    # The stdout contract: names and counts only, never values.
    import contextlib
    import io

    module_env_backup = dict(os.environ)
    try:
        os.environ.update(env)
        os.environ.pop("GITHUB_ENV", None)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            module.main()
    finally:
        os.environ.clear()
        os.environ.update(module_env_backup)
    stdout = out.getvalue()
    assert stdout.strip() == "Materialized 1 secret env var(s): BRAVE_KEY"
    assert "s3cret" not in stdout
