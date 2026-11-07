"""The red-test reviewer classifies non-new failures against ``origin/main``.

#1317's reviewer accepted 27 failing tests as "pre-existing environment
failures - verified via git stash". They were the branch being 17 commits behind
``main`` (the loop tests the branch head alone, CI tests the merge), which is
how #1344 started. The prompt must route that decision through ``origin/main``
and the merge tree, never ``git stash`` or the dev's own claim.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nebuko.engine.lib.config import WorkflowDefinition
from nebuko.engine.lint_cli import main as lint_main

REPO = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO / ".nebuko" / "workflows" / "red-test-review-loop.yaml"


@pytest.fixture(scope="module")
def definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(yaml.safe_load(WORKFLOW_PATH.read_text()))


def _prompt(definition: WorkflowDefinition, name: str) -> str:
    return next(step for step in definition.steps if step.name == name).prompt


class TestTheReviewerClassifiesFailuresAgainstMain:
    @pytest.mark.parametrize(
        "needle",
        [
            "git fetch origin main",
            "git rev-list --count HEAD..origin/main",
            "git worktree add --detach /tmp/rt-main origin/main",
            "git worktree add --detach /tmp/rt-merge origin/main",
            "git -C /tmp/rt-merge merge --no-edit",
            "PRE-EXISTING",
            "STALE BASE",
            "COLLATERAL",
        ],
    )
    def test_then_the_prompt_carries_the_check(self, definition, needle):
        assert needle in _prompt(definition, "review")

    def test_then_git_stash_is_ruled_out_as_a_check(self, definition):
        prompt = _prompt(definition, "review")
        assert "never classify with `git stash`" in " ".join(prompt.split())

    def test_then_the_devs_pre_existing_claim_is_not_accepted(self, definition):
        prompt = " ".join(_prompt(definition, "review").split())
        assert 'Never accept "pre-existing" or "environment failures"' in prompt

    def test_then_only_a_collateral_failure_rejects(self, definition):
        prompt = " ".join(_prompt(definition, "review").split())
        assert "fails on the merge tree: COLLATERAL" in prompt
        assert "Do not reject for it" in prompt


class TestTheDevDoesNotClassify:
    def test_then_the_dev_is_told_to_list_not_explain(self, definition):
        prompt = " ".join(_prompt(definition, "write_tests").split())
        assert "list their IDs and do not explain them" in prompt


def test_then_the_workflow_still_passes_the_linter(capsys):
    assert lint_main(["--strict", str(WORKFLOW_PATH)]) == 0, capsys.readouterr().out
