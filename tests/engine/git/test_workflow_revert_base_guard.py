"""Red test: workflow content equal to the base is main's own state arriving
via merge and must never be reverted as agent work (issue #1242, PR #1203
root cause).
"""

from __future__ import annotations

import unittest

from nebuko.engine.lib.agent_script.features.ensure_pr.workflow_patch import (
    revert_workflow_changes,
)


class TestRevertSkipsBaseIdenticalWorkflows(unittest.TestCase):
    def test_a_workflow_file_equal_to_the_base_is_not_restored(self):
        # given: a workflow path whose content equals origin/<base>'s content
        calls: list[list[str]] = []

        def run_git(args, **kwargs):
            calls.append(list(args))
            return ""

        # when: the revert runs with the base-comparison guard in place
        revert_workflow_changes(
            base_ref="origin/main",
            workflow_paths=[".github/workflows/ci.yaml"],
            workflow_matches_base=[".github/workflows/ci.yaml"],
            run_git=run_git,
        )

        # then: no restore/clean touches the base-identical file
        restore_calls = [c for c in calls if c[0] == "restore"]
        clean_calls = [c for c in calls if c[0] == "clean"]
        self.assertEqual(restore_calls, [])
        self.assertEqual(clean_calls, [])

    def test_a_real_workflow_delta_is_still_reverted(self):
        # given: a workflow path the agent actually changed
        calls: list[list[str]] = []

        def run_git(args, **kwargs):
            calls.append(list(args))
            return ""

        # when: the revert runs
        revert_workflow_changes(
            base_ref="origin/main",
            workflow_paths=[".github/workflows/ci.yaml"],
            workflow_matches_base=[],
            run_git=run_git,
        )

        # then: the file is restored from the base as before
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


if __name__ == "__main__":
    unittest.main()
