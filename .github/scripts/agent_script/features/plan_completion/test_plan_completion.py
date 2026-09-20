"""Tests for the plan-step completion marker (issue #613, G3).

All gh interactions are mocked at the subprocess boundary — no network in
unit tests.
"""

import unittest
from unittest.mock import patch

from workflow_state import ParentRef

from agent_script.features.plan_completion.plan_completion import (
    completion_marker_body,
    marker_already_present,
    post_terminal_plan_completion,
    resolve_completion_index,
)


def _parent(index=2):
    return ParentRef(
        workflow="plan-orchestration",
        step="sub_impl",
        issue_number=590,
        plan_step_index=index,
        plan_total_steps=6,
    )


class TestMarkerBody(unittest.TestCase):
    def test_zero_indexed_body(self):
        self.assertEqual(
            completion_marker_body(1),
            "<!-- bot-subtask-complete: 1 -->",
        )

    def test_negative_index_rejected(self):
        with self.assertRaises(ValueError):
            completion_marker_body(-1)


class TestResolveCompletionIndex(unittest.TestCase):
    def test_no_parent_is_noop(self):
        self.assertIsNone(resolve_completion_index(None, []))

    def test_no_plan_index_is_noop(self):
        parent = ParentRef(workflow="plan-orchestration", step="sub_impl")
        self.assertIsNone(resolve_completion_index(parent, []))

    def test_returns_zero_indexed(self):
        self.assertEqual(resolve_completion_index(_parent(index=3), []), 2)

    def test_duplicate_marker_is_noop(self):
        bodies = ["some comment <!-- bot-subtask-complete: 1 --> trailing"]
        self.assertIsNone(resolve_completion_index(_parent(index=2), bodies))

    def test_other_index_marker_does_not_block(self):
        bodies = ["<!-- bot-subtask-complete: 0 -->"]
        self.assertEqual(resolve_completion_index(_parent(index=2), bodies), 1)

    def test_empty_bodies_safe(self):
        self.assertEqual(
            resolve_completion_index(_parent(index=1), ["", None]), 0
        )


class TestMarkerAlreadyPresent(unittest.TestCase):
    def test_absent(self):
        self.assertFalse(marker_already_present(1, ["hello", ""]))

    def test_present(self):
        self.assertTrue(marker_already_present(1, ["x <!-- bot-subtask-complete: 1 --> y"]))


def _gh_ok(stdout="", returncode=0, stderr=""):
    from types import SimpleNamespace
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestPostTerminalPlanCompletion(unittest.TestCase):
    def test_no_parent_no_post(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.post_completion_marker"
        ) as post:
            self.assertFalse(post_terminal_plan_completion(None, 590))
            post.assert_not_called()

    def test_no_plan_index_no_post(self):
        parent = ParentRef(workflow="plan-orchestration", step="sub_impl")
        with patch(
            "agent_script.features.plan_completion.plan_completion.post_completion_marker"
        ) as post:
            self.assertFalse(post_terminal_plan_completion(parent, 590))
            post.assert_not_called()

    def test_marker_already_in_comments_skips(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.fetch_existing_bodies",
            return_value=["<!-- bot-subtask-complete: 1 -->"],
        ), patch(
            "agent_script.features.plan_completion.plan_completion.post_completion_marker",
            return_value=True,
        ) as post:
            self.assertFalse(post_terminal_plan_completion(_parent(index=2), 590))
            post.assert_not_called()

    def test_fresh_marker_posted(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.fetch_existing_bodies",
            return_value=["unrelated comment"],
        ), patch(
            "agent_script.features.plan_completion.plan_completion.post_completion_marker",
            return_value=True,
        ) as post:
            self.assertTrue(post_terminal_plan_completion(_parent(index=2), 590))
            post.assert_called_once_with(590, 1)

    def test_extra_bodies_catch_same_run_duplicate(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.post_completion_marker"
        ) as post:
            self.assertFalse(
                post_terminal_plan_completion(
                    _parent(index=2), 590,
                    extra_bodies=["<!-- bot-subtask-complete: 1 -->"],
                )
            )
            post.assert_not_called()

    def test_fetch_failure_still_posts(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.fetch_existing_bodies",
            return_value=None,
        ), patch(
            "agent_script.features.plan_completion.plan_completion.post_completion_marker",
            return_value=True,
        ) as post:
            self.assertTrue(post_terminal_plan_completion(_parent(index=2), 590))
            post.assert_called_once_with(590, 1)


class TestFetchAndPost(unittest.TestCase):
    def test_fetch_parses_bodies(self):
        import json
        stdout = json.dumps([{"body": "a"}, {"body": "b"}])
        with patch(
            "agent_script.features.plan_completion.plan_completion.subprocess.run",
            return_value=_gh_ok(stdout=stdout),
        ):
            from agent_script.features.plan_completion.plan_completion import (
                fetch_existing_bodies,
            )
            self.assertEqual(fetch_existing_bodies(7), ["a", "b"])

    def test_fetch_failure_returns_none(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.subprocess.run",
            return_value=_gh_ok(returncode=1, stderr="boom"),
        ):
            from agent_script.features.plan_completion.plan_completion import (
                fetch_existing_bodies,
            )
            self.assertIsNone(fetch_existing_bodies(7))

    def test_post_uses_gh_api(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.subprocess.run",
            return_value=_gh_ok(),
        ) as run, patch.dict(
            "os.environ", {"REPO_NAME": "acme/repo"}
        ):
            from agent_script.features.plan_completion.plan_completion import (
                post_completion_marker,
            )
            self.assertTrue(post_completion_marker(7, 1))
            args = run.call_args[0][0]
            self.assertEqual(
                args[:3],
                ["gh", "api", "repos/acme/repo/issues/7/comments"],
            )
            self.assertIn("-f", args)
            self.assertIn("body=<!-- bot-subtask-complete: 1 -->", args)

    def test_post_failure_returns_false(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.subprocess.run",
            return_value=_gh_ok(returncode=1, stderr="boom"),
        ):
            from agent_script.features.plan_completion.plan_completion import (
                post_completion_marker,
            )
            self.assertFalse(post_completion_marker(7, 1))


class TestBuildOutputTrigger(unittest.TestCase):
    """The trigger condition in build_output: terminal step + plan pointer."""

    def test_terminal_with_plan_pointer_posts(self):
        from workflow_state import WorkflowState
        from agent_script.features.build_output.build_output import (
            _maybe_post_plan_completion,
        )

        state = WorkflowState(
            workflow="dev-review-loop",
            step="review",
            agent="review",
            parent=_parent(index=2),
        )
        with patch(
            "agent_script.features.build_output.build_output.post_terminal_plan_completion"
        ) as post:
            _maybe_post_plan_completion(state, "")
            post.assert_called_once_with(_parent(index=2), 590)

    def test_non_terminal_step_skips(self):
        from workflow_state import WorkflowState
        from agent_script.features.build_output.build_output import (
            _maybe_post_plan_completion,
        )

        state = WorkflowState(
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            parent=_parent(index=2),
        )
        with patch(
            "agent_script.features.build_output.build_output.post_terminal_plan_completion"
        ) as post:
            _maybe_post_plan_completion(state, "review")
            post.assert_not_called()

    def test_no_plan_index_skips(self):
        from workflow_state import WorkflowState
        from agent_script.features.build_output.build_output import (
            _maybe_post_plan_completion,
        )

        state = WorkflowState(
            workflow="dev-review-loop",
            step="review",
            agent="review",
            parent=ParentRef(workflow="plan-orchestration", step="sub_impl"),
        )
        with patch(
            "agent_script.features.build_output.build_output.post_terminal_plan_completion"
        ) as post:
            _maybe_post_plan_completion(state, "")
            post.assert_not_called()

    def test_error_never_propagates(self):
        from workflow_state import WorkflowState
        from agent_script.features.build_output.build_output import (
            _maybe_post_plan_completion,
        )

        state = WorkflowState(
            workflow="dev-review-loop",
            step="review",
            agent="review",
            parent=_parent(index=2),
        )
        with patch(
            "agent_script.features.build_output.build_output.post_terminal_plan_completion",
            side_effect=RuntimeError("boom"),
        ):
            _maybe_post_plan_completion(state, "")  # must not raise


if __name__ == "__main__":
    unittest.main()