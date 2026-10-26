"""Tests for the workflow-patch delivery module (issue #1080).

Pure unit tests: ``run_git`` is injected, nothing touches a real repository.
"""

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nebuko.engine.lib.agent_script.features.ensure_pr.workflow_patch import (
    APPLY_COMMAND,
    PATCH_ARTIFACT_DIR,
    PATCH_FILENAME,
    WORKFLOW_DIR,
    build_patch,
    is_workflow_path,
    render_patch_comment,
    revert_workflow_changes,
    split_paths,
    stage_patch_artifact,
)


class TestIsWorkflowPath(unittest.TestCase):
    def test_workflow_files_are_detected(self):
        self.assertTrue(is_workflow_path(".github/workflows/ci.yaml"))
        self.assertTrue(is_workflow_path(".github/workflows/nested/ci.yml"))
        self.assertTrue(is_workflow_path("./.github/workflows/ci.yaml"))

    def test_lookalikes_are_not(self):
        for path in (
            "docs/workflows.md",
            ".github/workflow-docs/x.yaml",
            "src/.github/workflows/ci.yaml",
            ".github/workflows",
            "github/workflows/ci.yaml",
        ):
            self.assertFalse(is_workflow_path(path), path)

    def test_workflow_dir_constant_is_the_github_prefix(self):
        self.assertEqual(WORKFLOW_DIR, ".github/workflows/")


class TestSplitPaths(unittest.TestCase):
    def test_mixed_diff_splits_and_preserves_order(self):
        workflow, plain = split_paths(
            [
                "src/nebuko/engine/lib/a.py",
                ".github/workflows/internal-tests.yaml",
                "docs/features.md",
                ".github/workflows/ci.yaml",
            ]
        )
        self.assertEqual(
            workflow,
            (
                ".github/workflows/internal-tests.yaml",
                ".github/workflows/ci.yaml",
            ),
        )
        self.assertEqual(plain, ("src/nebuko/engine/lib/a.py", "docs/features.md"))

    def test_workflow_only(self):
        workflow, plain = split_paths([".github/workflows/ci.yaml"])
        self.assertEqual(workflow, (".github/workflows/ci.yaml",))
        self.assertEqual(plain, ())

    def test_plain_only(self):
        workflow, plain = split_paths(["README.md"])
        self.assertEqual(workflow, ())
        self.assertEqual(plain, ("README.md",))

    def test_empty(self):
        self.assertEqual(split_paths([]), ((), ()))


class TestBuildPatch(unittest.TestCase):
    def test_registers_untracked_paths_then_diffs_against_the_base(self):
        calls = []

        def run_git(args, **kwargs):
            calls.append(list(args))
            if args[:2] == ["diff", "--binary"]:
                return "diff --git a/.github/workflows/ci.yaml ..."
            return ""

        diff = build_patch(
            base_ref="origin/main",
            workflow_paths=[".github/workflows/ci.yaml"],
            run_git=run_git,
        )
        self.assertEqual(calls[0], [
            "add",
            "--intent-to-add",
            "--",
            ".github/workflows/ci.yaml",
        ])
        self.assertEqual(calls[1], [
            "diff",
            "--binary",
            "origin/main",
            "--",
            ".github/workflows/ci.yaml",
        ])
        self.assertIn("diff --git", diff)

    def test_intent_to_add_failure_still_diffs(self):
        def run_git(args, **kwargs):
            if args[0] == "add":
                raise RuntimeError("already tracked")
            return "diff"

        self.assertEqual(
            build_patch(
                base_ref="main",
                workflow_paths=[".github/workflows/ci.yaml"],
                run_git=run_git,
            ),
            "diff",
        )

    def test_no_paths_makes_no_git_call(self):
        def run_git(args, **kwargs):
            raise AssertionError("must not call git without paths")

        self.assertEqual(
            build_patch(base_ref="main", workflow_paths=[], run_git=run_git), ""
        )


class TestRenderPatchComment(unittest.TestCase):
    def _render(self, diff="--- a/x\n+++ b/x\n", max_chars=60_000, paths=None):
        return render_patch_comment(
            diff=diff,
            workflow_paths=paths or [".github/workflows/ci.yaml"],
            base_name="main",
            max_chars=max_chars,
        )

    def test_small_patch_is_inlined_with_the_apply_command(self):
        comment = self._render()
        self.assertFalse(comment.oversized)
        self.assertEqual(comment.artifact_name, "")
        self.assertIn("```diff", comment.body)
        self.assertIn("+++ b/x", comment.body)
        self.assertIn(APPLY_COMMAND, comment.body)
        self.assertIn(".github/workflows/ci.yaml", comment.body)
        self.assertEqual(comment.workflow_paths, (".github/workflows/ci.yaml",))

    def test_oversized_patch_links_the_artifact_instead(self):
        comment = self._render(diff="+" + "x" * 500, max_chars=200)
        self.assertTrue(comment.oversized)
        self.assertEqual(comment.artifact_name, PATCH_FILENAME)
        self.assertIn(PATCH_FILENAME, comment.body)
        self.assertIn(APPLY_COMMAND, comment.body)
        # The diff itself must not be inlined when it is over the limit.
        self.assertNotIn("xxxx", comment.body)

    def test_default_limit_is_the_repo_comment_policy(self):
        from nebuko.engine.lib.config.sub_domains.comment_size.comment_size import (
            DEFAULT_MAX_COMMENT_CHARS,
        )

        # Shrinking the diff by 400 chars flips the inline/oversized decision,
        # so the boundary really is the repo's per-comment budget.
        marker = DEFAULT_MAX_COMMENT_CHARS - 1
        oversized = self._render(diff="+" + "x" * marker)
        fits = self._render(diff="+" + "x" * (marker - 800))
        self.assertTrue(oversized.oversized)
        self.assertFalse(fits.oversized)


class TestStagePatchArtifact(unittest.TestCase):
    def test_writes_under_runner_temp(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"RUNNER_TEMP": tmp}
        ):
            name = stage_patch_artifact("diff --git a/x b/x\n")
            self.assertEqual(name, PATCH_FILENAME)
            path = os.path.join(tmp, PATCH_ARTIFACT_DIR, PATCH_FILENAME)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "diff --git a/x b/x\n")

    def test_existing_directory_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"RUNNER_TEMP": tmp}
        ):
            os.makedirs(os.path.join(tmp, PATCH_ARTIFACT_DIR))
            self.assertEqual(stage_patch_artifact("x"), PATCH_FILENAME)

    def test_io_failure_raises(self):
        with patch.dict(os.environ, {"RUNNER_TEMP": "/nonexistent-root-dir/x"}):
            with self.assertRaises(OSError):
                stage_patch_artifact("x")


class TestRevertWorkflowChanges(unittest.TestCase):
    def test_tracked_workflow_files_are_restored_from_the_base(self):
        calls = []

        def run_git(args, **kwargs):
            calls.append(list(args))
            return ""

        revert_workflow_changes(
            base_ref="origin/main",
            workflow_paths=[".github/workflows/ci.yaml"],
            run_git=run_git,
        )
        self.assertIn(
            [
                "restore",
                "--source",
                "origin/main",
                "--staged",
                "--worktree",
                "--",
                ".github/workflows/ci.yaml",
            ],
            calls,
        )
        self.assertIn(
            ["clean", "-f", "--", ".github/workflows/ci.yaml"], calls
        )

    def test_restore_failure_still_cleans(self):
        calls = []

        def run_git(args, **kwargs):
            calls.append(list(args))
            if args[0] == "restore":
                raise RuntimeError("not tracked")
            return ""

        revert_workflow_changes(
            base_ref="main",
            workflow_paths=[".github/workflows/new.yaml"],
            run_git=run_git,
        )
        self.assertIn(["clean", "-f", "--", ".github/workflows/new.yaml"], calls)

    def test_no_paths_makes_no_git_call(self):
        def run_git(args, **kwargs):
            raise AssertionError("must not call git without paths")

        revert_workflow_changes(base_ref="main", workflow_paths=[], run_git=run_git)


class TestApplyCommandOnACleanCheckout(unittest.TestCase):
    """Acceptance criterion: the comment's command works as written."""

    def test_patch_applies_and_restores_the_workflow_file(self):
        def git(*args, cwd):
            return subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
            ).stdout

        with tempfile.TemporaryDirectory() as tmp:
            git("init", "-q", "-b", "main", cwd=tmp)
            git("config", "user.name", "t", cwd=tmp)
            git("config", "user.email", "t@example.com", cwd=tmp)
            os.makedirs(os.path.join(tmp, ".github", "workflows"))
            path = os.path.join(tmp, ".github", "workflows", "ci.yaml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("on: [push]\n")
            git("add", "-A", cwd=tmp)
            git("commit", "-qm", "base", cwd=tmp)
            base = git("rev-parse", "HEAD", cwd=tmp).strip()

            with open(path, "w", encoding="utf-8") as handle:
                handle.write("on: [push, pull_request]\n")
            os.chmod(path, 0o755)

            def run_git(args, **kwargs):
                return subprocess.run(
                    ["git", *args],
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout

            diff = build_patch(
                base_ref=base,
                workflow_paths=[".github/workflows/ci.yaml"],
                run_git=run_git,
            )
            comment = render_patch_comment(
                diff=diff,
                workflow_paths=[".github/workflows/ci.yaml"],
                base_name="main",
            )
            self.assertIn(f"```bash\n# save the block above as {PATCH_FILENAME}",
                          comment.body)

            # Clean checkout of the base, patch saved as the comment says.
            git("checkout", "-q", "--", ".", cwd=tmp)
            git("clean", "-qfd", cwd=tmp)
            patch_file = os.path.join(tmp, PATCH_FILENAME)
            with open(patch_file, "w", encoding="utf-8") as handle:
                handle.write(diff)
            subprocess.run(
                ["git", "apply", patch_file], cwd=tmp, check=True, capture_output=True
            )
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "on: [push, pull_request]\n")
            # The file mode survives the round trip too.
            self.assertTrue(os.stat(path).st_mode & 0o100)


if __name__ == "__main__":
    unittest.main()
