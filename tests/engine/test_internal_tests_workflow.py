"""CI runs the documented test command on every pull request (issue #1311).

Nothing ran pytest before this job, so regressions landed on ``main`` unnoticed.
The job is pinned here so it cannot drift from ``AGENTS.md``: the documented
setup is ``uv sync --extra runtime`` into the repo's own ``.venv``, never the
engine checkout's ``.nebuko-engine/.venv`` (a stale venv makes the suite lie).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "internal-tests.yaml"
AGENTS_MD = REPO_ROOT / "AGENTS.md"

FULL_COMMAND = "tests src/nebuko/engine/lib"

# The workflow file is delivered as a ``workflow-patch.diff`` (AGENTS.md: the
# agent token has no ``workflow`` scope), so it is absent until a human applies
# the patch. The tests below then simply have nothing to check yet.
pytestmark = pytest.mark.skipif(
    not WORKFLOW_YAML.exists(),
    reason="internal-tests.yaml is delivered as a workflow patch",
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(workflow) -> dict[str, dict]:
    return {step.get("name"): step for step in workflow["jobs"]["pytest"]["steps"]}


def test_the_job_runs_on_pull_requests(workflow):
    # `on` is parsed as the boolean True by YAML 1.1 loaders.
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" in triggers


def test_the_setup_is_the_documented_uv_sync(steps):
    sync = steps["Sync dependencies"]
    assert sync["run"] == "uv sync --extra runtime"


def test_the_job_never_uses_the_engine_venv(steps):
    for step in steps.values():
        assert ".nebuko-engine" not in yaml.safe_dump(step)


def test_the_job_runs_the_documented_command(steps):
    run = steps["Run pytest"]["run"]
    assert run.startswith("uv run pytest ")
    assert FULL_COMMAND in run


def test_agents_md_documents_the_same_command():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert re.search(rf"^uv run pytest {FULL_COMMAND}\b", text, re.M)
    assert "No CI job runs pytest" not in text
