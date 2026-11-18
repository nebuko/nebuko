"""The reusable workflow runs for a repository_dispatch caller (ADR-0017 Lane D, #1099).

``run-task`` must accept the caller's ``nebuko-run`` dispatch, and the caller
template must forward the org-repo fields. The org token travels through
``secrets:`` only (ADR-0008), never ``with:``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from nebuko.engine.init import claude_code_repo as ccr

REUSABLE_PATH = Path(__file__).resolve().parents[2] / ".github/workflows/nebuko-workflow.yaml"
SHA = "a" * 40
CREDENTIAL = re.compile(r"(^|_)(pat|token)(_|$)", re.IGNORECASE)


def _reusable() -> dict:
    # PyYAML reads the bare ``on`` key as a boolean True.
    return yaml.safe_load(REUSABLE_PATH.read_text(encoding="utf-8"))


def test_run_task_condition_accepts_nebuko_run_dispatch() -> None:
    condition = _reusable()["jobs"]["run-task"]["if"]

    assert "github.event_name == 'repository_dispatch'" in condition
    assert "github.event.action == 'nebuko-run'" in condition


def test_org_repo_and_issue_are_non_secret_inputs() -> None:
    inputs = _reusable()[True]["workflow_call"]["inputs"]

    assert {"org_repo", "issue_number"} <= set(inputs)
    assert not any(CREDENTIAL.search(name) for name in inputs)


def test_gh_pat_is_a_declared_secret() -> None:
    secrets = _reusable()[True]["workflow_call"]["secrets"]

    assert "GH_PAT" in secrets


def test_caller_forwards_org_fields_and_maps_token_to_secret() -> None:
    caller = yaml.safe_load(ccr.render_workflow(SHA))
    agent = caller["jobs"]["agent"]

    assert {"prompt_body", "org_repo", "issue_number"} <= set(agent["with"])
    assert agent["secrets"]["GH_PAT"] == "${{ github.event.client_payload.gh_token }}"
    assert not any(CREDENTIAL.search(key) for key in agent["with"])
