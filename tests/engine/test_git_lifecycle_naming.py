"""Red tests for the git lifecycle naming and dirty-commit contract (#1153).

Covers: the shipped ``feature/issue-<n>-<title>`` branch name (the
``feature/<n>-<title>`` rename and its ``legacy_branch_name`` fallback proposed
by #1153 were never implemented) and the PR title helper.
"""

from __future__ import annotations

from nebuko.engine.lib.agent_script.features.ensure_pr.ensure_pr import pr_title
from nebuko.engine.lib.git.branch_scaffold import feature_branch_name


class TestBranchNaming:
    def test_feature_branch_uses_the_issue_convention(self):
        assert feature_branch_name(1153, "Prevent multiple branches") == (
            "feature/issue-1153-prevent-multiple-branches"
        )

    def test_feature_branch_slugs_special_characters(self):
        assert feature_branch_name(7, "Fix the Big Thing! (v2)") == (
            "feature/issue-7-fix-the-big-thing-v2"
        )


class TestPrTitle:
    def test_pr_title_is_issue_title_with_number(self):
        assert pr_title(1153, "Prevent multiple branches") == (
            "Prevent multiple branches (#1153)"
        )
