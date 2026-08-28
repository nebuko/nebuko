"""Tests for the deterministic PR fallback (graceful interruption, issue #489).

All git/gh interactions are mocked at the subprocess boundary — no network
or real git mutations in unit tests.
"""

import os
import unittest
from unittest.mock import patch

from agent_script.features.ensure_pr.ensure_pr import (
    EnsurePrResult,
    _default_base,
    _find_pr_for_branch,
    _issue_number_from_env,
    ensure_pr,
)


def _default_git_response(args, **kwargs):
    """Simulate a git CLI for the common ensure_pr scenarios."""
    joined = " ".join(args)
    if joined.startswith("remote get-url"):
        return "git@github.com:acme/repo.git"
    if joined == "remote":
        return "origin"
    if joined.startswith("rev-parse --abbrev-ref HEAD"):
        return "feature/issue-489-graceful"
    if joined.startswith("status --porcelain"):
        return " M some_file.py\n?? new_file.py"
    if joined.startswith("rev-parse") and "@{upstream}" in joined:
        return ""  # no upstream yet → push needs --set-upstream
    if joined.startswith("rev-parse"):
        return "abc123def456"
    return ""


def _default_gh_response(args, **kwargs):
    joined = " ".join(args)
    if joined.startswith("pr list"):
        return "https://github.com/acme/repo/pull/101"
    if joined.startswith("pr create"):
        return "https://github.com/acme/repo/pull/102"
    return ""


class TestHelpers(unittest.TestCase):
    def test_issue_number_valid(self):
        with patch.dict("os.environ", {"TARGET_NUMBER": "489"}):
            self.assertEqual(_issue_number_from_env(), 489)

    def test_issue_number_unset(self):
        saved = os.environ.pop("TARGET_NUMBER", None)
        try:
            self.assertIsNone(_issue_number_from_env())
        finally:
            if saved is not None:
                os.environ["TARGET_NUMBER"] = saved

    def test_issue_number_zero_is_treated_as_absent(self):
        # TARGET_NUMBER is normalized to "0" when no target issue exists —
        # a "Closes #0" would be a dangling reference, so treat it as None.
        with patch.dict("os.environ", {"TARGET_NUMBER": "0"}):
            self.assertIsNone(_issue_number_from_env())

    def test_issue_number_invalid(self):
        with patch.dict("os.environ", {"TARGET_NUMBER": "not-an-int"}):
            self.assertIsNone(_issue_number_from_env())

    def test_default_base_prefers_github_base_ref(self):
        with patch.dict("os.environ", {"GITHUB_BASE_REF": "develop"}):
            self.assertEqual(_default_base(), "develop")

    def test_default_base_queries_api_when_base_ref_empty(self):
        # On issues/issue_comment/workflow_call triggers GITHUB_BASE_REF is
        # unset — the repo's default branch must come from the API. ``gh``
        # resolves the repo from the ambient git context, so the call uses
        # the {owner}/{repo} template.
        saved = os.environ.pop("GITHUB_BASE_REF", None)

        def gh_out(args, **k):
            if args[0] == "api":
                return '"develop"'
            return ""

        with patch("agent_script.features.ensure_pr.ensure_pr._run_gh",
                   side_effect=gh_out) as gh_mock:
            try:
                self.assertEqual(_default_base(), "develop")
            finally:
                if saved is not None:
                    os.environ["GITHUB_BASE_REF"] = saved
        api_calls = [
            c for c in gh_mock.call_args_list
            if c.args and c.args[0][0] == "api"
        ]
        self.assertEqual(len(api_calls), 1)
        self.assertIn("repos/{owner}/{repo}", " ".join(api_calls[0].args[0]))

    def test_default_base_falls_back_to_main_on_api_failure(self):
        saved = os.environ.pop("GITHUB_BASE_REF", None)
        with patch(
            "agent_script.features.ensure_pr.ensure_pr._run_gh",
            side_effect=RuntimeError("gh api failed"),
        ):
            try:
                self.assertEqual(_default_base(), "main")
            finally:
                if saved is not None:
                    os.environ["GITHUB_BASE_REF"] = saved

    def test_find_pr_for_branch_returns_url(self):
        with patch(
            "agent_script.features.ensure_pr.ensure_pr._run_gh",
            side_effect=_default_gh_response,
        ):
            url = _find_pr_for_branch("feature/x")
        self.assertEqual(url, "https://github.com/acme/repo/pull/101")

    def test_find_pr_for_branch_none_when_empty(self):
        with patch(
            "agent_script.features.ensure_pr.ensure_pr._run_gh",
            return_value="",
        ):
            self.assertIsNone(_find_pr_for_branch("feature/x"))


class TestEnsurePr(unittest.TestCase):
    """End-to-end behaviour of ensure_pr with subprocess mocked out."""

    def _mock_runners(self, git_out=None, gh_out=None, git_exc=None):
        """Patch _run_git/_run_gh; git_exc makes _run_git raise."""
        def git_side_effect(*a, **k):
            if git_exc is not None:
                raise git_exc
            return git_out(*a, **k) if git_out is not None else ""

        return (
            patch(
                "agent_script.features.ensure_pr.ensure_pr._run_git",
                side_effect=git_side_effect,
            ),
            patch(
                "agent_script.features.ensure_pr.ensure_pr._run_gh",
                side_effect=gh_out if gh_out is not None else _default_gh_response,
            ),
        )

    def test_no_remote_skips(self):
        git_p, gh_p = self._mock_runners(git_out=lambda *a, **k: "")
        with git_p, gh_p:
            result = ensure_pr()
        self.assertTrue(result.skipped)
        self.assertEqual(result.message, "no git remote")

    def test_detached_head_skips(self):
        def git_out(args, **k):
            joined = " ".join(args)
            if joined == "remote":
                return "origin"
            if joined.startswith("rev-parse --abbrev-ref HEAD"):
                return "HEAD"
            return ""

        git_p, gh_p = self._mock_runners(git_out=git_out)
        with git_p, gh_p:
            result = ensure_pr()
        self.assertTrue(result.skipped)
        self.assertEqual(result.message, "detached HEAD")

    def test_success_path_commits_pushes_and_finds_existing_pr(self):
        git_p, gh_p = self._mock_runners(git_out=_default_git_response)
        with git_p as git_mock, gh_p:
            result = ensure_pr()
        self.assertFalse(result.skipped)
        self.assertFalse(result.error)
        self.assertTrue(result.pushed)
        self.assertEqual(result.pr_url, "https://github.com/acme/repo/pull/101")
        self.assertEqual(result.commit_hash, "abc123def456")
        # Commit was staged & pushed with the upstream flag.
        calls = [str(c) for c in git_mock.call_args_list]
        self.assertTrue(any("add" in c for c in calls))
        self.assertTrue(any("--set-upstream" in c for c in calls))

    def test_opens_new_pr_when_none_exists(self):
        def gh_out(args, **k):
            joined = " ".join(args)
            if joined.startswith("pr list"):
                return ""
            return "https://github.com/acme/repo/pull/102"

        git_p, gh_p = self._mock_runners(
            git_out=_default_git_response, gh_out=gh_out
        )
        env = {"TARGET_NUMBER": "489", "GITHUB_BASE_REF": "main"}
        with git_p, gh_p as gh_mock, patch.dict("os.environ", env):
            result = ensure_pr()
        self.assertFalse(result.error)
        self.assertEqual(result.pr_url, "https://github.com/acme/repo/pull/102")
        create_calls = [
            c
            for c in gh_mock.call_args_list
            if c.args and c.args[0][:2] == ["pr", "create"]
        ]
        self.assertEqual(len(create_calls), 1)
        # The issue link travels via the PR body ("Closes #N").
        create_args = create_calls[0].args[0]
        body = create_args[create_args.index("--body") + 1]
        self.assertIn("Closes #489", body)

    def test_dry_run_makes_no_mutations(self):
        git_p, gh_p = self._mock_runners(git_out=_default_git_response)
        with git_p as git_mock, gh_p as gh_mock:
            result = ensure_pr(dry_run=True)
        self.assertFalse(result.error)
        self.assertEqual(result.message, "[dry run] would open PR")
        git_calls = [str(c) for c in git_mock.call_args_list]
        self.assertFalse(any("push" in c for c in git_calls))
        self.assertFalse(any("commit" in c for c in git_calls))
        # gh must not be invoked either.
        self.assertEqual(gh_mock.call_count, 0)

    def test_error_is_captured_not_raised(self):
        git_p, gh_p = self._mock_runners(git_exc=RuntimeError("git exploded"))
        with git_p, gh_p:
            result = ensure_pr()
        self.assertTrue(result.error)
        self.assertIn("git exploded", result.message)

    def test_result_is_immutable(self):
        result = EnsurePrResult(skipped=True)
        with self.assertRaises(Exception):
            result.skipped = False


if __name__ == "__main__":
    unittest.main()
