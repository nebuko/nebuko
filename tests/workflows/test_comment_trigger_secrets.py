"""The comment trigger forwards the Claude Code token (issue #1389).

``nebuko-workflow.yaml`` declares, masks and exports ``CLAUDE_CODE_OAUTH_TOKEN``,
but a ``workflow_call`` only receives the secrets its caller names — so the
trigger workflow has to pass it, or a comment-triggered claude-code run starts
without a credential. Secret values travel through ``secrets:`` only, never
``with:`` (ADR-0008).
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_PATH = Path(".github/workflows/interntal-trigger-nebuko-on-comment.yaml")
REUSABLE_PATH = Path(".github/workflows/nebuko-workflow.yaml")


def _workflow(path: Path) -> dict:
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def test_trigger_forwards_the_claude_code_token() -> None:
    job = _workflow(TRIGGER_PATH)["jobs"]["agent"]
    assert (
        job["secrets"]["CLAUDE_CODE_OAUTH_TOKEN"]
        == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    )


def test_trigger_job_still_passes_the_provider_keys() -> None:
    job = _workflow(TRIGGER_PATH)["jobs"]["agent"]
    assert job["secrets"]["MISTRAL_API_KEY"] == "${{ secrets.MISTRAL_API_KEY }}"
    assert (
        job["secrets"]["CHEAPERINFERENCE_API_KEY"]
        == "${{ secrets.CHEAPERINFERENCE_API_KEY }}"
    )


def test_reusable_workflow_declares_it_optional() -> None:
    secrets = _workflow(REUSABLE_PATH)[True]["workflow_call"]["secrets"]
    assert secrets["CLAUDE_CODE_OAUTH_TOKEN"]["required"] is False


def test_reusable_workflow_exports_it_only_when_non_empty() -> None:
    # An empty export would shadow a token the runner already provides.
    steps = _workflow(REUSABLE_PATH)["jobs"]["run-task"]["steps"]
    step = next(s for s in steps if s.get("name") == "Export Claude Code token")
    assert "CLAUDE_CODE_OAUTH_TOKEN_SECRET" in step["env"]
    assert "::add-mask::" in step["run"]
    assert 'if [ -n "$CLAUDE_CODE_OAUTH_TOKEN_SECRET" ]' in step["run"]
