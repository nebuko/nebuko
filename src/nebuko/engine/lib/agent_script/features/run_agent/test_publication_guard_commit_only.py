"""Red tests for the commit-only publication guard (issue #1150).

With `git push` denied, "branch not pushed" is always true — the guard must
stop recalling for push-related reasons and ask the agent only to commit.
"""

import unittest

from agent_script.features.run_agent.publication_guard import (
    publication_recall_prompt,
    recallable_reasons,
)


class TestCommitOnlyRecallPrompt(unittest.TestCase):
    """The recall prompt asks for a commit, never a push."""

    def test_prompt_says_do_not_push(self):
        prompt = publication_recall_prompt(["dirty_tree"])
        self.assertIn("Commit your changes", prompt)
        self.assertIn("not** push", prompt)
        self.assertNotIn("Push the branch", prompt)

    def test_prompt_keeps_the_engine_opens_the_pr_step(self):
        prompt = publication_recall_prompt(["dirty_tree"])
        self.assertIn("engine opens the pull request", prompt)


class TestCommitOnlyRecallReasons(unittest.TestCase):
    """Push-related reasons no longer trigger recall."""

    def test_clean_tree_on_unpushed_branch_does_not_trigger_recall(self):
        # A clean tree with unpushed commits is the normal end state now:
        # the engine pushes after the run, so this must not recall.
        self.assertEqual(recallable_reasons(("unpushed_commits",)), ())
        self.assertEqual(recallable_reasons(("missing_pr",)), ())

    def test_uncommitted_changes_still_trigger_recall(self):
        self.assertEqual(recallable_reasons(("dirty_tree",)), ("dirty_tree",))

    def test_workflow_patch_stays_non_recallable(self):
        self.assertEqual(recallable_reasons(("workflow_patch",)), ())


if __name__ == "__main__":
    unittest.main()
