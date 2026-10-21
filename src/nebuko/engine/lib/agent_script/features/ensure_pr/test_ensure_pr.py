"""Tests for the deterministic PR fallback (graceful interruption, issue #489).

All git/gh interactions are mocked at the subprocess boundary — no network
or real git mutations in unit tests.
"""

import os
import subprocess
import unittest
from unittest.mock import patch

from agent_script.features.ensure_pr.ensure_pr import (
    ENGINE_OWNED_PATHS,
    EnsurePrResult,
    PublicationState,
    _default_base,
    _find_pr_for_branch,
    _issue_number_from_env,
    _repo_root,
    _run_gh,
    _run_git,
    check_publication,
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

    def test_default_base_falls_back_to_main_on_api_timeout(self):
        # A hung gh api query must also degrade to "main" (never escape
        # into the ensure_pr error path), per issue #489 review feedback.
        from subprocess import TimeoutExpired

        saved = os.environ.pop("GITHUB_BASE_REF", None)
        with patch(
            "agent_script.features.ensure_pr.ensure_pr._run_gh",
            side_effect=TimeoutExpired(cmd=["gh", "api"], timeout=120),
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

    def test_repo_root_reads_gfa_repo_root(self):
        with patch.dict("os.environ", {"GFA_REPO_ROOT": "/real/checkout"}):
            self.assertEqual(_repo_root(), "/real/checkout")

    def test_repo_root_absent_or_blank_is_none(self):
        # Absent or blank ⇒ inherit the process cwd (previous behaviour).
        saved = os.environ.pop("GFA_REPO_ROOT", None)
        try:
            self.assertIsNone(_repo_root())
            os.environ["GFA_REPO_ROOT"] = "   "
            self.assertIsNone(_repo_root())
        finally:
            os.environ.pop("GFA_REPO_ROOT", None)
            if saved is not None:
                os.environ["GFA_REPO_ROOT"] = saved

    def test_run_git_runs_in_the_repo_root(self):
        """#766: git must target the checkout, not the host's temporary cwd."""
        fake = subprocess.CompletedProcess(["git", "remote"], 0, "origin\n", "")
        with patch.object(subprocess, "run", return_value=fake) as run_mock, patch.dict(
            "os.environ", {"GFA_REPO_ROOT": "/real/checkout"}
        ):
            self.assertEqual(_run_git(["remote"]), "origin")
        self.assertEqual(run_mock.call_args.kwargs["cwd"], "/real/checkout")

    def test_run_gh_runs_in_the_repo_root(self):
        fake = subprocess.CompletedProcess(["gh", "pr", "list"], 0, "url\n", "")
        with patch.object(subprocess, "run", return_value=fake) as run_mock, patch.dict(
            "os.environ", {"GFA_REPO_ROOT": "/real/checkout"}
        ):
            self.assertEqual(_run_gh(["pr", "list"]), "url")
        self.assertEqual(run_mock.call_args.kwargs["cwd"], "/real/checkout")

    def test_run_git_inherits_cwd_without_repo_root(self):
        saved = os.environ.pop("GFA_REPO_ROOT", None)
        fake = subprocess.CompletedProcess(["git", "remote"], 0, "origin\n", "")
        try:
            with patch.object(subprocess, "run", return_value=fake) as run_mock:
                _run_git(["remote"])
            self.assertIsNone(run_mock.call_args.kwargs["cwd"])
        finally:
            if saved is not None:
                os.environ["GFA_REPO_ROOT"] = saved

    def test_run_git_honours_an_explicit_cwd(self):
        fake = subprocess.CompletedProcess(["git", "remote"], 0, "origin\n", "")
        with patch.object(subprocess, "run", return_value=fake) as run_mock, patch.dict(
            "os.environ", {"GFA_REPO_ROOT": "/real/checkout"}
        ):
            _run_git(["remote"], cwd="/explicit")
        self.assertEqual(run_mock.call_args.kwargs["cwd"], "/explicit")


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

    def test_gh_calls_share_one_transport(self):
        """#805: PR lookup and PR creation ride a single transport so the write
        pacing/budget state (and its backoff) is shared across the whole flow."""
        recorded = []

        class _Result:
            ok = True
            status = 200
            detail = ""

            def __init__(self, payload):
                self.payload = payload

        class _FakeTransport:
            def call(self, args, *, mutative=False, cwd=None, timeout=None):
                recorded.append((list(args), mutative))
                if args[:2] == ["pr", "create"]:
                    return _Result("https://github.com/acme/repo/pull/102")
                if args[:1] == ["api"]:
                    return _Result('"main"')
                return _Result("")

        instances = []

        def factory(*a, **k):
            transport = _FakeTransport()
            instances.append(transport)
            return transport

        env = {"TARGET_NUMBER": "805", "GITHUB_BASE_REF": ""}
        with patch(
            "agent_script.features.ensure_pr.ensure_pr._run_git",
            side_effect=_default_git_response,
        ), patch(
            "agent_script.features.ensure_pr.ensure_pr.GitHubTransport",
            side_effect=factory,
        ), patch.dict("os.environ", env):
            result = ensure_pr()

        self.assertFalse(result.error)
        self.assertEqual(result.pr_url, "https://github.com/acme/repo/pull/102")
        # Exactly one transport instance, shared by the lookup and the write.
        self.assertEqual(len(instances), 1)
        by_head = [args[:2] for args, _ in recorded]
        self.assertIn(["pr", "list"], by_head)
        self.assertIn(["pr", "create"], by_head)
        mutative = {tuple(args[:2]): m for args, m in recorded}
        self.assertTrue(mutative[("pr", "create")])
        self.assertFalse(mutative[("pr", "list")])

    def test_run_gh_raises_when_transport_cannot_deliver(self):
        """A dropped content-creation write must surface as a failure, never an
        empty success that silently loses the PR (issue #805)."""

        class _FailedResult:
            ok = False
            status = 403
            detail = "secondary rate limit"
            payload = ""

        class _FailTransport:
            def call(self, *a, **k):
                return _FailedResult()

        with patch(
            "agent_script.features.ensure_pr.ensure_pr.GitHubTransport",
            return_value=_FailTransport(),
        ):
            with self.assertRaises(RuntimeError):
                _run_gh(["pr", "create"])

    def test_result_is_immutable(self):
        result = EnsurePrResult(skipped=True)
        with self.assertRaises(Exception):
            result.skipped = False


def _publication_git(status="", upstream_count=None, base_count="0", branch="feature/x"):
    """Simulate git for ``check_publication`` scenarios.

    ``upstream_count`` is None for a branch with no upstream (git errors);
    the base-branch comparison then answers with ``base_count``.
    """

    def run(args, **kwargs):
        joined = " ".join(args)
        if joined == "remote":
            return "origin"
        if joined.startswith("rev-parse --abbrev-ref HEAD"):
            return branch
        if joined.startswith("status --porcelain"):
            return status
        if joined.startswith("rev-list --count @{u}..HEAD"):
            if upstream_count is None:
                raise RuntimeError("no upstream configured")
            return upstream_count
        if joined.startswith("rev-list --count"):
            return base_count
        raise AssertionError(f"unexpected git call: {joined}")

    return run


class TestCheckPublication(unittest.TestCase):
    """The publication check behind the approved-write guard."""

    #: Sentinel for "the PR lookup raises".
    _RAISES = object()

    def _check(self, git, pr="https://github.com/acme/repo/pull/7"):
        """Run the check; ``pr`` is the PR-lookup result (None = no PR)."""
        with patch(
            "agent_script.features.ensure_pr.ensure_pr._run_git", side_effect=git
        ), patch(
            "agent_script.features.ensure_pr.ensure_pr._find_pr_for_branch",
            **(
                {"side_effect": RuntimeError("gh pr list failed")}
                if pr is self._RAISES
                else {"return_value": pr}
            ),
        ), patch(
            "agent_script.features.ensure_pr.ensure_pr._default_base",
            return_value="main",
        ):
            return check_publication()

    def test_clean_tree_and_pushed_branch_with_pr_is_published(self):
        state = self._check(_publication_git(upstream_count="0"))
        self.assertTrue(state.published)
        self.assertEqual(state.reasons, ())
        self.assertEqual(state.skip_reason, "")

    def test_dirty_tree_counts_as_unpublished(self):
        state = self._check(
            _publication_git(status=" M logo/nebuko-logo.py", upstream_count="0")
        )
        self.assertFalse(state.published)
        self.assertEqual(state.reasons, ("dirty_tree",))
        self.assertEqual(state.dirty_paths, ("logo/nebuko-logo.py",))

    def test_engine_owned_handoff_path_never_counts_as_dirty(self):
        self.assertIn(".nebuko/handoff.md", ENGINE_OWNED_PATHS)
        state = self._check(
            _publication_git(status="?? .nebuko/handoff.md", upstream_count="0")
        )
        self.assertTrue(state.published)
        self.assertEqual(state.dirty_paths, ())

    def test_untracked_work_is_dirty(self):
        # A file the agent created is exactly "uncommitted work"; git already
        # hides gitignored files from --porcelain.
        state = self._check(
            _publication_git(status="?? new_module.py", upstream_count="0")
        )
        self.assertEqual(state.dirty_paths, ("new_module.py",))

    def test_unpushed_commits_count_as_unpublished(self):
        state = self._check(_publication_git(upstream_count="2"))
        self.assertFalse(state.published)
        self.assertIn("unpushed_commits", state.reasons)
        self.assertNotIn("missing_pr", state.reasons)

    def test_branch_never_pushed_counts_via_the_base_comparison(self):
        state = self._check(
            _publication_git(upstream_count=None, base_count="3"), pr=""
        )
        self.assertIn("unpushed_commits", state.reasons)

    def test_never_pushed_branch_with_nothing_on_it_is_clean(self):
        # Nothing ahead of the base and no PR: an empty WIP PR would be noise,
        # so this is deliberately not "missing_pr".
        state = self._check(
            _publication_git(upstream_count=None, base_count="0"), pr=""
        )
        self.assertTrue(state.published)

    def test_branch_work_without_a_pr_is_unpublished(self):
        state = self._check(_publication_git(upstream_count="1"), pr="")
        self.assertFalse(state.published)
        self.assertEqual(state.reasons, ("unpushed_commits", "missing_pr"))

    def test_no_remote_skips(self):
        def git(args, **kwargs):
            if " ".join(args) == "remote":
                return ""
            raise AssertionError("must not probe further without a remote")

        state = self._check(git)
        self.assertTrue(state.published)
        self.assertEqual(state.skip_reason, "no git remote")

    def test_detached_head_skips(self):
        state = self._check(_publication_git(branch="HEAD"))
        self.assertTrue(state.published)
        self.assertEqual(state.skip_reason, "detached HEAD")

    def test_pr_lookup_failure_degrades_to_a_skip(self):
        state = self._check(_publication_git(upstream_count="1"), pr=self._RAISES)
        self.assertEqual(state.skip_reason, "pr lookup failed")

    def test_never_raises(self):
        def git(args, **kwargs):
            raise RuntimeError("git exploded")

        state = self._check(git)
        self.assertTrue(state.published)
        self.assertTrue(state.skip_reason)

    def test_state_is_immutable(self):
        with self.assertRaises(Exception):
            PublicationState(published=True).published = False


if __name__ == "__main__":
    unittest.main()
