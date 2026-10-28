"""Tests for the deterministic PR fallback (graceful interruption, issue #489).

All git/gh interactions are mocked at the subprocess boundary — no network
or real git mutations in unit tests.
"""

import contextlib
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_script.features.ensure_pr.ensure_pr import (
    ENGINE_OWNED_PATHS,
    EnsurePrResult,
    _default_base,
    _find_pr_for_branch,
    _issue_number_from_env,
    _dirty_paths,
    _needs_push,
    _prepare_publish,
    _repo_root,
    _run_gh,
    _run_git,
    ensure_pr,
    pr_title,
)
from agent_script.features.ensure_pr.ensure_pr import PublishSplit
from agent_script.features.ensure_pr.workflow_patch import (
    PATCH_FILENAME,
    WORKFLOW_DIR,
)


def _porcelain_z(text: str) -> str:
    """Render human porcelain as ``git status --porcelain -z`` does.

    ``-z`` drops the space between the XY columns and the path and uses NUL
    terminators, so ``" M a.py"`` becomes ``" M" + "a.py" + ``\\0``. Text that
    already is in the raw form (it contains a NUL — e.g. a rename block built
    by :func:`_porcelain_z_renames`) is passed through untouched, because the
    ``line[:2] + line[3:]`` rewrite below would eat the first character.
    """
    if "\0" in text:
        return text
    return "".join(line[:2] + line[3:] + "\0" for line in text.splitlines())


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
        return _porcelain_z(" M some_file.py\n?? new_file.py")
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


class TestDirtyPaths(unittest.TestCase):
    def test_engine_owned_handoff_path_is_never_agent_work(self):
        self.assertIn(".nebuko/handoff.md", ENGINE_OWNED_PATHS)
        with patch(
            "agent_script.features.ensure_pr.ensure_pr._run_git",
            return_value=_porcelain_z("?? .nebuko/handoff.md\n M src/a.py"),
        ):
            self.assertEqual(_dirty_paths(), ("src/a.py",))


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
        # "commit" appears in the dry-run *log line* only; inspect the argv.
        self.assertFalse(
            any("commit" in c.args[0] for c in git_mock.call_args_list if c.args)
        )
        self.assertFalse(any("git add" in c for c in git_calls))
        # gh may only *read* (the default-branch lookup); no mutative call.
        mutative = [
            c for c in gh_mock.call_args_list if c.kwargs.get("mutative")
        ]
        self.assertEqual(mutative, [])

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


# ── Workflow-patch publication (issue #1080) ───────────────────────────────

_WORKFLOW = ".github/workflows/internal-tests.yaml"
_WORKFLOW_ORIGIN = ".github/workflows/old-checks.yaml"
_CODE = "src/nebuko/engine/lib/thing.py"


def _porcelain_z_renames(*specs: tuple[str, str, str]) -> str:
    """Render rename/copy entries as ``git status --porcelain -z`` does.

    ``specs`` are ``(code, new_path, origin_path)``; the raw form is two
    NUL-separated fields — ``R  new\\0old\\0`` — with no `` -> `` anywhere.
    The origin field carries no status prefix of its own.
    """
    return "".join(f"{code} {new}\0{origin}\0" for code, new, origin in specs)


def _patch_git(
    *,
    status=f" M {_CODE}\n M {_WORKFLOW}\n",
    committed="",
    base_ref="origin/main",
    patch="diff --git a/x b/x\n",
    on_push=None,
    after_rebuild=None,
    unmerged="",
):
    """Git fake for the path-split publication flow.

    ``status`` is ``git status --porcelain``, ``committed`` the paths touched by
    unpushed commits (both endpoints of a name-only diff), ``patch`` what
    ``git diff --binary`` returns, ``after_rebuild`` what ``git status``
    reports once ``_prepare_publish`` has reset the publish commit. Every argv
    is recorded in ``calls``.
    """
    calls: list[list[str]] = []
    state = {"base": None}

    def run_git(args, **kwargs):
        calls.append(list(args))
        joined = " ".join(args)
        if joined.startswith("remote get-url"):
            return "acme/repo"
        if joined == "remote":
            return "origin"
        if joined.startswith("rev-parse --abbrev-ref HEAD"):
            return "feature/issue-1080"
        if "commit" in args:
            return ""
        if joined.startswith("rev-parse --verify"):
            return "1" if args[-1].split("^{")[0] == base_ref else ""
        if joined.startswith("status --porcelain"):
            # After a ``reset --mixed`` the working tree holds what the rebuild
            # left behind — explicitly supplied by ``after_rebuild``.
            if state["base"]:
                return _porcelain_z(after_rebuild or "")
            return _porcelain_z(status)
        if joined.startswith("diff --name-only"):
            return committed
        if joined.startswith("ls-files --unmerged"):
            return unmerged
        if joined.startswith("rev-list --count @{u}..HEAD"):
            return "0"
        if joined.startswith("rev-list --count"):
            return "1" if committed else "0"
        if joined.startswith("rev-list --max-parents=0"):
            return "deadbeef"
        if joined.startswith("merge-base"):
            return "base000"
        if joined.startswith("diff --binary"):
            return patch
        if joined.startswith("log --format=%H"):
            return "sha1"
        if joined.startswith("add"):
            return ""
        if joined.startswith("commit"):
            return ""
        if joined.startswith("restore"):
            return ""
        if joined.startswith("clean"):
            return ""
        if args[0] == "push":
            if on_push is not None:
                return on_push(list(args))
            return ""
        if joined.startswith("reset"):
            # ``--mixed`` keeps the work in the working tree while restoring
            # the index to the reset target; the fake switches to
            # ``after_rebuild`` (what the rebuild leaves behind).
            state["base"] = args[-1]
            return ""
        if joined.startswith("rev-parse --short"):
            return "abc1234"
        if joined.startswith("rev-parse"):
            return "sha1"
        raise AssertionError(f"unexpected git call: {joined}")

    run_git.calls = calls
    return run_git


class _GhRecorder:
    """Route gh api/PR calls; records every argv."""

    def __init__(self, pr_lookup="", pr_create="https://github.com/acme/repo/pull/9"):
        self.pr_lookup = pr_lookup
        self.pr_create = pr_create
        self.recorded: list[tuple[list[str], bool]] = []

    def factory(self, *a, **k):
        return self

    def call(self, args, *, mutative=False, cwd=None, timeout=None):
        self.recorded.append((list(args), mutative))

        class _Result:
            ok = True
            status = 200
            detail = ""

            def __init__(self, payload):
                self.payload = payload

        if args[:2] == ["pr", "list"]:
            return _Result(self.pr_lookup)
        if args[:2] == ["pr", "create"]:
            return _Result(self.pr_create)
        if args[:1] == ["api"]:
            joined = " ".join(args)
            if "/comments" in joined:
                return _Result("https://github.com/acme/repo/issues/1080#issuecomment-1")
            return _Result('"main"')
        return _Result("")


def _patch_ensure_pr(git, gh, env=None, artifact_dir=None):
    """Context manager running ``ensure_pr`` against the patch-flow fakes."""
    patchers = [
        patch("agent_script.features.ensure_pr.ensure_pr._run_git", git),
        patch("agent_script.features.ensure_pr.ensure_pr.GitHubTransport", gh.factory),
    ]
    if artifact_dir is not None:
        patchers.append(
            patch(
                "agent_script.features.ensure_pr.workflow_patch.PATCH_ARTIFACT_DIR",
                artifact_dir,
            )
        )

    stack = contextlib.ExitStack()
    for p in patchers:
        stack.enter_context(p)
    stack.enter_context(patch.dict("os.environ", env or {}, clear=False))
    return stack


class TestWorkflowPatchPublication(unittest.TestCase):
    """A workflow delta is delivered as a patch, never pushed (#1080)."""

    def _run(self, git, gh, env=None, artifact_dir=None):
        with _patch_ensure_pr(git, gh, env, artifact_dir):
            return ensure_pr()

    def test_mixed_diff_opens_the_pr_and_comments_the_patch(self):
        git = _patch_git()
        gh = _GhRecorder()
        result = self._run(git, gh, env={"TARGET_NUMBER": "1080"})

        self.assertFalse(result.error)
        self.assertEqual(result.pr_url, "https://github.com/acme/repo/pull/9")
        self.assertEqual(result.patch_paths, (_WORKFLOW,))
        self.assertIn("issuecomment-1", result.patch_comment_url)
        # The code was committed without the workflow path: staging names the
        # path explicitly (never ``add -A``/``add .``), so a tree carrying both
        # cannot smuggle the workflow file into the commit.
        adds = [c for c in git.calls if c and c[0] == "add"]
        self.assertIn(["add", "--", _CODE], adds)
        # No staging invocation ever names a workflow path, and nothing stages
        # the whole tree (`add -A` / `add .`) which would.
        for add in adds:
            if add[1:2] == ["--"]:
                self.assertNotIn(_WORKFLOW, add, add)
            self.assertNotIn("-A", add)
            self.assertFalse(add[1:2] == ["."], add)
        # The workflow file was reverted and the tree left clean.
        self.assertTrue(any(c[:1] == ["restore"] and _WORKFLOW in c for c in git.calls))
        self.assertTrue(any(c[:1] == ["clean"] and _WORKFLOW in c for c in git.calls))
        # The patch comment was posted mutatively.
        self.assertTrue(
            any("/comments" in " ".join(args) and mutative for args, mutative in gh.recorded)
        )

    def test_no_git_push_or_commit_call_ever_carries_a_workflow_path(self):
        """The token boundary must hold for every mutating invocation."""
        git = _patch_git()
        gh = _GhRecorder()
        result = self._run(git, gh, env={"TARGET_NUMBER": "1080"})

        def workflow_path_in(args):
            return any(str(a).startswith(WORKFLOW_DIR) for a in args)

        for args in git.calls:
            if args and args[0] in {"commit", "push"}:
                self.assertFalse(workflow_path_in(args), args)
            if args[:2] == ["add", "-A"]:
                self.fail("git add -A would stage workflow files")
        # Even the subprocess argv strings shown to a reviewer are clean.
        pushes = [c for c in git.calls if c and c[0] == "push"]
        self.assertTrue(pushes)
        for push in pushes:
            self.assertNotIn(_WORKFLOW, " ".join(push))
        self.assertEqual(result.patch_paths, (_WORKFLOW,))

    def test_workflow_only_diff_posts_on_the_issue_without_a_pr(self):
        git = _patch_git(status=f" M {_WORKFLOW}\n")
        gh = _GhRecorder()
        result = self._run(git, gh, env={"TARGET_NUMBER": "1080"})

        self.assertFalse(result.error)
        self.assertEqual(result.pr_url, "")
        self.assertIn("issuecomment-1", result.patch_comment_url)
        self.assertEqual(result.patch_paths, (_WORKFLOW,))
        self.assertEqual(result.message, "workflow patch delivered")
        # No push and no PR for an all-workflow delta. The empty checkpoint
        # commit is harmless: with no plain delta it is never pushed.
        self.assertEqual([c for c in git.calls if c and c[0] == "push"], [])
        # No PR *write* for an all-workflow delta (the read-only lookup stays).
        self.assertFalse(
            any(args[:2] == ["pr", "create"] for args, _ in gh.recorded)
        )
        # …and the tree is left clean for the guard's dirty-tree check.
        self.assertTrue(any(c[:1] == ["clean"] and _WORKFLOW in c for c in git.calls))

    def test_contaminated_unpushed_commit_is_rebuilt_without_the_workflow_file(self):
        # The agent committed code and a workflow file, staged them, then its
        # push was rejected. ``git diff`` (no HEAD) reports the staged paths;
        # the workflow file is *not* in ``git status`` (it is committed), which
        # is exactly the case that used to slip into the rebuilt commit.
        git = _patch_git(
            status="",
            committed=f"{_CODE}\n{_WORKFLOW}\n",
            after_rebuild=f" M {_CODE}\n",
        )
        gh = _GhRecorder()
        result = self._run(git, gh, env={"TARGET_NUMBER": "1080"})

        self.assertFalse(result.error)
        self.assertEqual(
            [c for c in git.calls if c and c[0] == "reset"],
            [["reset", "--mixed", "base000"]],
        )
        # The index is scrubbed of the workflow path before anything is
        # committed (a staged workflow file would ride along otherwise).
        self.assertIn(
            ["restore", "--staged", "--", _WORKFLOW], git.calls
        )
        commits = [c for c in git.calls if "commit" in c]
        self.assertEqual(len(commits), 1)
        self.assertNotIn(_WORKFLOW, commits[0])
        pushes = [c for c in git.calls if c and c[0] == "push"]
        self.assertTrue(pushes)
        self.assertIn("--force-with-lease", pushes[0])

    def test_oversized_patch_is_staged_as_an_artifact(self):
        big = "diff --git a/x b/x\n" + "+" + "x" * 65_000
        git = _patch_git(status=f" M {_WORKFLOW}\n", patch=big)
        gh = _GhRecorder()
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                git, gh, env={"TARGET_NUMBER": "1080"}, artifact_dir=tmp
            )
            staged = os.path.join(tmp, PATCH_FILENAME)
            self.assertTrue(os.path.exists(staged), os.listdir(tmp))
        self.assertFalse(result.error)
        self.assertEqual(result.patch_artifact, PATCH_FILENAME)
        # The comment links the artifact instead of inlining 65k of diff.
        bodies = [
            " ".join(args) for args, mutative in gh.recorded if mutative
        ]
        self.assertTrue(bodies)
        self.assertIn(PATCH_FILENAME, "\n".join(bodies))
        self.assertNotIn("x" * 1000, "\n".join(bodies))

    def test_patch_delivery_failure_keeps_the_tree_dirty(self):
        """Never discard workflow changes we failed to deliver."""
        git = _patch_git(status=f" M {_WORKFLOW}\n")
        gh = _GhRecorder()

        def boom(*a, **k):
            raise RuntimeError("github down")

        gh.call = boom
        result = self._run(git, gh, env={"TARGET_NUMBER": "1080"})
        self.assertTrue(result.error)
        self.assertFalse(any(c[:1] == ["clean"] for c in git.calls))
        self.assertFalse(any(c[:1] == ["restore"] for c in git.calls))

    def test_workflow_only_rename_is_delivered_as_a_patch_without_a_push(self):
        """A workflow-only rename must not stage nothing and fail the commit."""
        git = _patch_git(
            status=_porcelain_z_renames(("R ", _WORKFLOW, _WORKFLOW_ORIGIN))
        )
        gh = _GhRecorder()
        result = self._run(git, gh, env={"TARGET_NUMBER": "1080"})

        self.assertFalse(result.error, result.message)
        self.assertEqual(set(result.patch_paths), {_WORKFLOW, _WORKFLOW_ORIGIN})
        self.assertIn("issuecomment-1", result.patch_comment_url)
        self.assertEqual(result.pr_url, "")
        self.assertEqual([c for c in git.calls if c and c[0] == "push"], [])
        # Neither endpoint may be pushed or committed as plain work. The
        # ``--intent-to-add`` registration inside ``build_patch`` is exempt: it
        # only teaches git about untracked paths for the diff, never commits.
        for args in git.calls:
            if args and args[0] in {"commit", "push"}:
                self.assertNotIn(_WORKFLOW, args, args)
                self.assertNotIn(_WORKFLOW_ORIGIN, args, args)

    def test_workflow_rename_mixed_with_code_pushes_only_the_code(self):
        git = _patch_git(
            status=_porcelain_z(f" M {_CODE}")
            + _porcelain_z_renames(("R ", _WORKFLOW, _WORKFLOW_ORIGIN))
        )
        gh = _GhRecorder()
        result = self._run(git, gh, env={"TARGET_NUMBER": "1080"})

        self.assertFalse(result.error, result.message)
        self.assertEqual(set(result.patch_paths), {_WORKFLOW, _WORKFLOW_ORIGIN})
        self.assertEqual(result.pr_url, "https://github.com/acme/repo/pull/9")
        adds = [
            c
            for c in git.calls
            if c and c[0] == "add" and c[1:2] != ["--intent-to-add"]
        ]
        self.assertIn(["add", "--", _CODE], adds)
        for add in adds:
            self.assertNotIn(_WORKFLOW, add, add)
            self.assertNotIn(_WORKFLOW_ORIGIN, add, add)

    def test_dry_run_does_not_touch_workflow_files(self):
        git = _patch_git()
        gh = _GhRecorder()
        with _patch_ensure_pr(git, gh, env={"TARGET_NUMBER": "1080"}):
            result = ensure_pr(dry_run=True)
        self.assertFalse(result.error)
        # The plan is reported, nothing is done.
        self.assertEqual(result.patch_paths, (_WORKFLOW,))
        mutating = [
            c
            for c in git.calls
            if c and c[0] in {"commit", "push", "reset", "restore", "clean"}
        ]
        self.assertEqual(mutating, [])
        self.assertFalse(any(mutative for _, mutative in gh.recorded))


class TestPublicationNaming(unittest.TestCase):
    """The commit and the PR are named after the issue, never "WIP checkpoint"."""

    def _run(self, *, env, status=f" M {_CODE}\n", issue_view="", **kwargs):
        git = _patch_git(status=status, **kwargs)
        gh = _GhRecorder()
        original_call = gh.call

        def call(args, **kw):
            if args[:2] == ["issue", "view"]:
                gh.recorded.append((list(args), kw.get("mutative", False)))

                class _R:
                    ok = True
                    status = 200
                    detail = ""
                    payload = issue_view

                return _R()
            return original_call(args, **kw)

        gh.call = call
        with _patch_ensure_pr(git, gh, env):
            result = ensure_pr(**self.ensure_kwargs)
        return result, git, gh

    def setUp(self):
        self.ensure_kwargs = {}

    @staticmethod
    def _commit_message(git):
        commits = [c for c in git.calls if "commit" in c]
        assert len(commits) == 1, commits
        return commits[0][commits[0].index("-m") + 1]

    @staticmethod
    def _created_pr(gh):
        creates = [args for args, _ in gh.recorded if args[:2] == ["pr", "create"]]
        assert len(creates) == 1, creates
        args = creates[0]
        return args[args.index("--title") + 1], args[args.index("--body") + 1]

    def test_the_issue_title_names_the_commit_and_the_pr(self):
        result, git, gh = self._run(
            env={"TARGET_NUMBER": "1080", "ISSUE_TITLE": "Deliver workflow patches"}
        )

        self.assertFalse(result.error, result.message)
        title, body = self._created_pr(gh)
        self.assertEqual(title, "Deliver workflow patches (#1080)")
        self.assertNotIn("WIP", title)
        self.assertNotIn("time budget", body)
        message = self._commit_message(git)
        self.assertTrue(message.startswith("Deliver workflow patches (#1080)"), message)
        self.assertNotIn("WIP", message)

    def test_the_issue_title_is_looked_up_when_not_given(self):
        result, _, gh = self._run(
            env={"TARGET_NUMBER": "1080", "ISSUE_TITLE": ""},
            issue_view="Title from GitHub",
        )

        self.assertFalse(result.error, result.message)
        self.assertEqual(self._created_pr(gh)[0], "Title from GitHub (#1080)")

    def test_the_branch_names_the_pr_without_an_issue_title(self):
        result, _, gh = self._run(env={"TARGET_NUMBER": "1080", "ISSUE_TITLE": ""})

        self.assertFalse(result.error, result.message)
        self.assertEqual(self._created_pr(gh)[0], "feature/issue-1080")

    def test_an_explicit_title_wins(self):
        self.ensure_kwargs = {"issue_title": "Explicit"}
        result, _, gh = self._run(env={"TARGET_NUMBER": "1080", "ISSUE_TITLE": "Env"})

        self.assertFalse(result.error, result.message)
        self.assertEqual(self._created_pr(gh)[0], "Explicit (#1080)")

    def test_an_interrupted_run_says_so_in_the_body_only(self):
        self.ensure_kwargs = {"interrupted": True}
        result, git, gh = self._run(
            env={"TARGET_NUMBER": "1080", "ISSUE_TITLE": "Deliver workflow patches"}
        )

        self.assertFalse(result.error, result.message)
        title, body = self._created_pr(gh)
        self.assertEqual(title, "Deliver workflow patches (#1080)")
        self.assertIn("interrupted", body)
        self.assertIn("interrupted", self._commit_message(git))

    def test_unresolved_conflicts_are_never_published(self):
        result, git, gh = self._run(
            env={"TARGET_NUMBER": "1080", "ISSUE_TITLE": "T"},
            unmerged="100644 abc 1\tsrc/a.py\n100644 def 2\tsrc/a.py\n",
        )

        self.assertTrue(result.error)
        self.assertIn("src/a.py", result.message)
        self.assertFalse(any("commit" in c for c in git.calls))
        self.assertFalse(any(c and c[0] == "push" for c in git.calls))
        self.assertFalse(any(args[:2] == ["pr", "create"] for args, _ in gh.recorded))

    def test_pr_title_helper(self):
        self.assertEqual(pr_title(1153, "Prevent multiple branches"),
                         "Prevent multiple branches (#1153)")
        self.assertEqual(pr_title(None, "No number"), "No number")
        self.assertEqual(pr_title(7, "  "), "")


class TestRealGitTokenBoundary(unittest.TestCase):
    """End-to-end against real git: the pushed tree never holds a workflow file.

    Acceptance criterion 7 — "a test pins that the agent token is never used to
    push .github/workflows/*" — is only meaningful if it runs real ``git``, so
    this drives the actual subprocess argv through a temp repository whose
    ``origin`` is a bare repo we can inspect after the push.
    """

    def _git(self, *args, cwd):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout

    def test_the_pushed_commit_has_no_workflow_path(self):
        from agent_script.features.ensure_pr import ensure_pr as mod

        workflow = ".github/workflows/internal-tests.yaml"
        code = "src/thing.py"

        with tempfile.TemporaryDirectory() as root:
            remote = os.path.join(root, "origin.git")
            work = os.path.join(root, "work")
            os.makedirs(work)
            self._git("init", "-q", "--bare", "-b", "main", remote, cwd=root)
            self._git("init", "-q", "-b", "main", work, cwd=root)
            for key, value in (("user.name", "t"), ("user.email", "t@e.com")):
                self._git("config", key, value, cwd=work)
            self._git("remote", "add", "origin", remote, cwd=work)
            os.makedirs(os.path.join(work, ".github", "workflows"))
            os.makedirs(os.path.join(work, "src"))
            for path, body in (
                (workflow, "on: [push]\n"),
                (code, "x = 1\n"),
            ):
                with open(os.path.join(work, path), "w", encoding="utf-8") as fh:
                    fh.write(body)
            self._git("add", "-A", cwd=work)
            self._git("commit", "-qm", "base", cwd=work)
            self._git("push", "-q", "-u", "origin", "main", cwd=work)

            # Agent work: a plain file *and* a workflow file, both left dirty.
            with open(os.path.join(work, code), "w", encoding="utf-8") as fh:
                fh.write("x = 2\n")
            with open(os.path.join(work, workflow), "w", encoding="utf-8") as fh:
                fh.write("on: [push, pull_request]\n")

            posted: list[str] = []
            class _Transport:
                def call(self, args, *, mutative=False, cwd=None, timeout=None):
                    joined = " ".join(args)

                    class _R:
                        ok = True
                        status = 200
                        detail = ""
                        payload = ""

                    if "default_branch" in joined:
                        _R.payload = "main"
                    elif args[:2] == ["pr", "list"]:
                        _R.payload = ""
                    elif args[:2] == ["pr", "create"]:
                        _R.payload = "https://github.com/acme/repo/pull/9"
                    elif "/comments" in joined:
                        _R.payload = "https://github.com/acme/repo/issues/1080#c1"
                        posted.append(joined)
                    return _R()

            real_git = mod._run_git

            def git_here(args, **kwargs):
                # Run every git command in the temp repo.
                return real_git(args, cwd=work, **kwargs)

            with patch.object(mod, "_run_git", git_here), patch.object(
                mod, "GitHubTransport", lambda *a, **k: _Transport()
            ), patch.dict(
                os.environ,
                {
                    "TARGET_NUMBER": "1080",
                    "GITHUB_REPOSITORY": "acme/repo",
                },
                clear=False,
            ):
                result = mod.ensure_pr()


            self.assertFalse(result.error, result.message)
            self.assertTrue(result.patch_paths)

            # The remote really received the code and really did not receive
            # the workflow file — checked against git, not a mock argv. The
            # *pushed commit* is what the token carried: the base commit's
            # workflow file is irrelevant here.
            import subprocess as _sp

            pushed_paths = _sp.run(
                [
                    "git",
                    "-C",
                    remote,
                    "show",
                    "--name-only",
                    "--format=",
                    "main",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split()
            self.assertIn(code, pushed_paths)
            self.assertNotIn(workflow, pushed_paths)

            # The patch was posted and applies to a clean checkout.
            self.assertTrue(posted)
            self.assertIn("workflow-patch.diff", " ".join(posted))
            self.assertFalse(
                self._git("status", "--porcelain", cwd=work).strip(),
                "ensure_pr must leave the tree clean",
            )


class TestRealGitNewWorkflowFileBecomesCommittedPatch(unittest.TestCase):
    """Acceptance spec for the post_hook git push engine feature.

    WHEN a new file appears under ``.github/workflows/``
    THEN the engine converts it into a patch
    AND commits the patch and deletes the workflow file.
    """

    def _git(self, *args, cwd):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout

    def test_new_workflow_file_is_patched_committed_and_deleted(self):
        from agent_script.features.ensure_pr import ensure_pr as mod

        workflow = ".github/workflows/new-pipeline.yaml"

        with tempfile.TemporaryDirectory() as root:
            remote = os.path.join(root, "origin.git")
            work = os.path.join(root, "work")
            os.makedirs(work)
            self._git("init", "-q", "--bare", "-b", "main", remote, cwd=root)
            self._git("init", "-q", "-b", "main", work, cwd=root)
            for key, value in (("user.name", "t"), ("user.email", "t@e.com")):
                self._git("config", key, value, cwd=work)
            self._git("remote", "add", "origin", remote, cwd=work)
            os.makedirs(os.path.join(work, "src"))
            with open(os.path.join(work, "src", "a.py"), "w", encoding="utf-8") as fh:
                fh.write("a = 1\n")
            self._git("add", "-A", cwd=work)
            self._git("commit", "-qm", "base", cwd=work)
            self._git("push", "-q", "-u", "origin", "main", cwd=work)

            # WHEN: a brand-new (untracked) file under .github/workflows/
            os.makedirs(os.path.join(work, ".github", "workflows"))
            with open(os.path.join(work, workflow), "w", encoding="utf-8") as fh:
                fh.write("on: [push]\njobs: {}\n")

            class _Transport:
                def call(self, args, *, mutative=False, cwd=None, timeout=None):
                    joined = " ".join(args)

                    class _R:
                        ok = True
                        status = 200
                        detail = ""
                        payload = ""

                    if "default_branch" in joined:
                        _R.payload = "main"
                    elif args[:2] == ["pr", "list"]:
                        _R.payload = ""
                    elif args[:2] == ["pr", "create"]:
                        _R.payload = "https://github.com/acme/repo/pull/1"
                    elif "/comments" in joined:
                        _R.payload = "https://github.com/acme/repo/issues/1#c1"
                    return _R()

            real_git = mod._run_git

            def git_here(args, **kwargs):
                return real_git(args, cwd=work, **kwargs)

            with patch.object(mod, "_run_git", git_here), patch.object(
                mod, "GitHubTransport", lambda *a, **k: _Transport()
            ), patch.dict(
                os.environ,
                {"TARGET_NUMBER": "1", "GITHUB_REPOSITORY": "acme/repo"},
                clear=False,
            ):
                result = mod.ensure_pr()

            self.assertFalse(result.error, result.message)

            # THEN: the workflow file is converted to a patch ...
            self.assertIn(workflow, result.patch_paths)

            # ... the workflow file is deleted from the worktree ...
            self.assertFalse(
                os.path.exists(os.path.join(work, workflow)),
                "the workflow file must be deleted after the patch is built",
            )

            # ... and the patch is committed in the repository.
            tracked = self._git(
                "ls-files", "--", "nebuko-task-logs/workflow-patch.diff", cwd=work
            ).strip()
            self.assertTrue(
                tracked,
                "the engine must commit the workflow patch ("
                + PATCH_FILENAME
                + ") instead of only delivering it as a comment/artifact",
            )

            # ... and pushed with a PR, so a human can fetch the branch and
            # apply it (degraded delivery): the remote branch carries the
            # patch file, never the workflow file.
            self.assertTrue(result.pushed)
            self.assertEqual(result.pr_url, "https://github.com/acme/repo/pull/1")
            branch = self._git("rev-parse", "--abbrev-ref", "HEAD", cwd=work).strip()
            pushed = self._git(
                "ls-tree", "-r", "--name-only", branch, cwd=remote
            ).split()
            self.assertIn("nebuko-task-logs/workflow-patch.diff", pushed)
            self.assertNotIn(workflow, pushed)


class TestRealGitMissingLocalBase(unittest.TestCase):
    """#1058/#1084: a checkout with no local base branch must not look clean.

    The guard compared against the base *name* (``main``). When only
    ``origin/main`` exists that diff fails, and the failure used to be read as
    "nothing unpushed" — so committed workflow paths were pushed with the
    token that GitHub rejects, which is exactly how #1058 lost its push.
    """

    def _git(self, *args, cwd):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout

    def _repo_without_local_main(self, root):
        """Bare origin + work clone on a feature branch with committed work."""
        remote = os.path.join(root, "origin.git")
        work = os.path.join(root, "work")
        os.makedirs(work)
        self._git("init", "-q", "--bare", "-b", "main", remote, cwd=root)
        self._git("clone", "-q", remote, work, cwd=root)
        for key, value in (("user.name", "t"), ("user.email", "t@e.com")):
            self._git("config", key, value, cwd=work)
        os.makedirs(os.path.join(work, ".github", "workflows"))
        os.makedirs(os.path.join(work, "src"))
        for path, body in (
            (self.WORKFLOW, "on: [push]\n"),
            (self.CODE, "x = 1\n"),
        ):
            with open(os.path.join(work, path), "w", encoding="utf-8") as fh:
                fh.write(body)
        self._git("add", "-A", cwd=work)
        self._git("commit", "-qm", "base", cwd=work)
        self._git("push", "-q", "origin", "main", cwd=work)

        # Only origin/main survives: the repro's `fatal: ambiguous argument
        # 'main'` needs no local main in the checkout.
        self._git("checkout", "-q", "-b", "feature/issue-1058", cwd=work)
        self._git("branch", "-D", "main", cwd=work)
        with self.assertRaises(subprocess.CalledProcessError):
            self._git("rev-parse", "--verify", "main^{commit}", cwd=work)

        # The agent *committed* its work before its own push — no upstream.
        with open(os.path.join(work, self.WORKFLOW), "w", encoding="utf-8") as fh:
            fh.write("on: [push, pull_request]\n")
        with open(os.path.join(work, self.CODE), "w", encoding="utf-8") as fh:
            fh.write("x = 2\n")
        self._git("add", "-A", cwd=work)
        self._git("commit", "-qm", "agent work", cwd=work)
        return remote, work

    WORKFLOW = ".github/workflows/internal-tests.yaml"
    CODE = "src/thing.py"

    def _transport(self, posted):
        class _Transport:
            def call(self, args, *, mutative=False, cwd=None, timeout=None):
                class _R:
                    ok = True
                    status = 200
                    detail = ""
                    payload = ""

                joined = " ".join(args)
                if "default_branch" in joined:
                    # The name is unresolvable in this checkout on purpose.
                    _R.payload = "main"
                elif args[:2] == ["pr", "list"]:
                    _R.payload = ""
                elif args[:2] == ["pr", "create"]:
                    _R.payload = "https://github.com/acme/repo/pull/9"
                elif "/comments" in joined:
                    _R.payload = "https://github.com/acme/repo/issues/1058#c1"
                    posted.append(joined)
                return _R()

        return _Transport()

    def test_ensure_pr_keeps_the_committed_workflow_path_out_of_the_push(self):
        from agent_script.features.ensure_pr import ensure_pr as mod

        with tempfile.TemporaryDirectory() as root:
            remote, work = self._repo_without_local_main(root)
            real_git = mod._run_git

            def git_here(args, **kwargs):
                return real_git(args, cwd=work, **kwargs)

            posted: list[str] = []
            with patch.object(mod, "_run_git", git_here), patch.object(
                mod, "GitHubTransport", lambda *a, **k: self._transport(posted)
            ), patch.dict(
                os.environ,
                {"TARGET_NUMBER": "1058", "GITHUB_REPOSITORY": "acme/repo"},
                clear=False,
            ):
                result = mod.ensure_pr()
                self.assertFalse(result.error, result.message)

            self.assertTrue(result.patch_paths)
            self.assertTrue(posted)
            pushed = subprocess.run(
                ["git", "-C", remote, "show", "--name-only", "--format=",
                 "feature/issue-1058"],
                capture_output=True, text=True, check=True,
            ).stdout.split()
            self.assertIn(self.CODE, pushed)
            self.assertNotIn(self.WORKFLOW, pushed)


class TestRealGitBaseRefResolution(unittest.TestCase):
    """Unit-level contract of the shared base-ref resolver and the guard."""

    def test_resolve_base_ref_prefers_the_remote_tracking_ref(self):
        from agent_script.features.ensure_pr import ensure_pr as mod

        calls: list[str] = []

        def git(args, **kwargs):
            calls.append(" ".join(args))
            return "sha"

        with patch.object(mod, "_run_git", git):
            self.assertEqual(mod._resolve_base_ref("main", "origin"), "origin/main")
        self.assertEqual(calls, ["rev-parse --verify origin/main^{commit}"])

    def test_resolve_base_ref_falls_back_then_raises(self):
        from agent_script.features.ensure_pr import ensure_pr as mod

        seen: list[str] = []

        def git(args, **kwargs):
            ref = args[-1]
            seen.append(ref)
            if "HEAD" not in ref:
                raise RuntimeError(f"unknown ref {ref}")
            return "sha"

        with patch.object(mod, "_run_git", git):
            self.assertEqual(mod._resolve_base_ref("main", "origin"), "origin/HEAD")
        self.assertEqual(
            seen,
            ["origin/main^{commit}", "main^{commit}", "origin/HEAD^{commit}"],
        )

        def nothing(args, **kwargs):
            raise RuntimeError("unknown ref")

        with patch.object(mod, "_run_git", nothing):
            with self.assertRaises(RuntimeError):
                mod._resolve_base_ref("main", "origin")

    def test_unpushed_commit_paths_raises_when_the_diff_fails(self):
        from agent_script.features.ensure_pr import ensure_pr as mod

        def git(args, **kwargs):
            raise RuntimeError("fatal: ambiguous argument 'main'")

        with patch.object(mod, "_run_git", git):
            with self.assertRaises(RuntimeError):
                mod._unpushed_commit_paths("main", "feature/x")

if __name__ == "__main__":
    unittest.main()


class TestCommittedOnlyPublication(unittest.TestCase):
    """Committed-but-unpushed plain work is published as-is (issue #1180).

    The guard saw ``unpushed_commits`` + ``missing_pr`` with a clean tree and
    ``ensure_pr`` answered ``"workflow patch delivered"`` without pushing or
    opening anything. These tests lock the fix: committed plain paths are
    published by pushing the branch as-is and opening the PR.
    """

    COMMITTED = "src/x.py"

    def _git(self, committed=COMMITTED):
        # Clean tree (no dirty paths); the delta lives only in unpushed commits.
        return _patch_git(status="", committed=committed)

    def test_given_clean_tree_with_one_unpushed_plain_commit_when_preparing_publish_then_committed_plain_paths_are_reported(self):
        git = self._git()
        with patch("agent_script.features.ensure_pr.ensure_pr._run_git", git):
            split = _prepare_publish("origin/main", "feature/issue-1180", "origin/main")
        self.assertEqual(
            split,
            PublishSplit(
                workflow_paths=(),
                plain_paths=(),
                republished=False,
                committed_plain_paths=(self.COMMITTED,),
            ),
        )
        # The tree is left untouched: no reset to merge base for plain work.
        self.assertFalse(any(c and c[0] == "reset" for c in git.calls))

    def test_given_committed_only_split_when_checking_needs_push_then_true_and_workflow_only_false(self):
        self.assertTrue(
            _needs_push(
                PublishSplit(
                    workflow_paths=(),
                    plain_paths=(),
                    republished=False,
                    committed_plain_paths=(self.COMMITTED,),
                )
            )
        )
        self.assertFalse(
            _needs_push(
                PublishSplit(
                    workflow_paths=(".github/workflows/ci.yml",),
                    plain_paths=(),
                    republished=False,
                )
            )
        )

    def test_given_one_unpushed_plain_commit_and_no_pr_when_running_ensure_pr_then_branch_is_pushed_and_pr_opened(self):
        git = self._git()
        gh = _GhRecorder(pr_lookup="")
        with _patch_ensure_pr(git, gh, env={"TARGET_NUMBER": "1180"}):
            result = ensure_pr()

        self.assertFalse(result.error)
        self.assertTrue(result.pushed)
        self.assertTrue(result.pr_url, "a PR must be opened for committed-only work")
        self.assertTrue(result.message.startswith("PR opened: "))
        self.assertTrue(any(c and c[0] == "push" for c in git.calls))
        create_calls = [
            args for args, _mutative in gh.recorded if args[:2] == ["pr", "create"]
        ]
        self.assertEqual(len(create_calls), 1)
        body = create_calls[0][create_calls[0].index("--body") + 1]
        self.assertIn("Closes #1180", body)

    def test_given_no_dirty_paths_no_unpushed_work_and_no_pr_when_running_ensure_pr_then_message_is_nothing_to_publish(self):
        git = self._git(committed="")
        gh = _GhRecorder(pr_lookup="")
        with _patch_ensure_pr(git, gh, env={"TARGET_NUMBER": "1180"}):
            result = ensure_pr()

        self.assertFalse(result.error)
        self.assertFalse(result.pushed)
        self.assertEqual(result.pr_url, "")
        self.assertEqual(
            result.message,
            "nothing to publish: no plain delta and no PR opened",
        )
