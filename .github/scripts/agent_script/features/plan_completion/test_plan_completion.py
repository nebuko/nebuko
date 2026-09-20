"""Tests for the plan-step completion marker (issue #613, G3).

All gh interactions are mocked at the subprocess boundary — no network in
unit tests.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from workflow_state import ParentRef, WorkflowState

from agent_script.features.build_output.build_output import (
    _maybe_post_plan_completion,
)
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

    def test_fetch_parses_two_pages(self):
        """A --paginate stream is two concatenated JSON arrays."""
        import json
        page1 = json.dumps([{"body": "a"}, {"body": "b"}])
        page2 = json.dumps([{"body": "c"}, {"body": "d"}])
        with patch(
            "agent_script.features.plan_completion.plan_completion.subprocess.run",
            return_value=_gh_ok(stdout=page1 + page2),
        ):
            from agent_script.features.plan_completion.plan_completion import (
                fetch_existing_bodies,
            )
            self.assertEqual(fetch_existing_bodies(7), ["a", "b", "c", "d"])

    def test_fetch_parses_marker_bodies_with_brackets(self):
        """Bracket-bearing marker bodies (incl. HTML comment brackets)
        survive the top-level-span scanner."""
        import json
        page1 = json.dumps(
            [{"body": "<!-- bot-subtask-complete: 0 -->"}, {"body": "[link] x"}]
        )
        page2 = json.dumps([{"body": "plain"}])
        with patch(
            "agent_script.features.plan_completion.plan_completion.subprocess.run",
            return_value=_gh_ok(stdout=page1 + page2),
        ):
            from agent_script.features.plan_completion.plan_completion import (
                fetch_existing_bodies,
            )
            self.assertEqual(
                fetch_existing_bodies(7),
                ["<!-- bot-subtask-complete: 0 -->", "[link] x", "plain"],
            )

    def test_two_page_duplicate_marker_not_posted(self):
        """Round-3 spec: marker only on the second page is caught — no post.

        Without multi-page parsing the old single json.loads raised, the
        guard fell through to post-anyway, and engine recalls duplicated the
        marker on long, polluted issues.
        """
        import json
        page1 = json.dumps([{"body": f"noise {i}"} for i in range(30)])
        marker = completion_marker_body(0)
        page2 = json.dumps([{"body": "tail"}, {"body": marker}])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1:3] == ["api", "--paginate"]:
                return _gh_ok(stdout=page1 + page2)
            return _gh_ok(stdout="")

        with patch(
            "agent_script.features.plan_completion.plan_completion.subprocess.run",
            side_effect=fake_run,
        ), patch.dict("os.environ", {"REPO_NAME": "acme/repo"}):
            from agent_script.features.plan_completion.plan_completion import (
                post_terminal_plan_completion,
            )
            self.assertFalse(post_terminal_plan_completion(_parent(index=1), 7))
        self.assertEqual(
            calls,
            [["gh", "api", "--paginate", "repos/acme/repo/issues/7/comments"]],
        )

    def test_fetch_failure_returns_none(self):
        with patch(
            "agent_script.features.plan_completion.plan_completion.subprocess.run",
            return_value=_gh_ok(returncode=1, stderr="boom"),
        ):
            from agent_script.features.plan_completion.plan_completion import (
                fetch_existing_bodies,
            )
            self.assertIsNone(fetch_existing_bodies(7))

    def test_parser_unit_cases(self):
        from agent_script.features.plan_completion.plan_completion import (
            _parse_paginated_json_arrays,
        )
        self.assertEqual(_parse_paginated_json_arrays(""), [])
        self.assertEqual(
            _parse_paginated_json_arrays('[{"body": "a"}][{"body": "b"}]'),
            [{"body": "a"}, {"body": "b"}],
        )
        # strings containing brackets/quotes/escapes must not desync the scan
        tricky = '[{"body": "\u005c" ] [", "x": "["}][{"body": "[ok]"}]'
        self.assertEqual(
            _parse_paginated_json_arrays(tricky),
            [{"body": '" ] [', "x": "["}, {"body": "[ok]"}],
        )
        # unparseable span → None (guard degrades to post-anyway branch)
        self.assertIsNone(_parse_paginated_json_arrays("[{broken]"))

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

    def test_terminal_signal_posts_completed_index_not_refreshed(self):
        """Return-leg boundary: the COMPLETED index wins over the state pointer.

        At the boundary the state pointer already carries the *next* step
        (index 2 here) while WORKFLOW_NEXT is non-empty; only the resolver's
        WORKFLOW_STEP_TERMINAL + COMPLETED_PLAN_STEP_INDEX signal names the
        step that just finished (index 1).
        """
        import os
        from workflow_state import WorkflowState
        from agent_script.features.build_output.build_output import (
            _maybe_post_plan_completion,
        )

        state = WorkflowState(
            workflow="drl",
            step="implement",
            agent="dev",
            parent=_parent(index=2),  # refreshed pointer (next step)
        )
        with patch.dict(os.environ, {
            "WORKFLOW_STEP_TERMINAL": "true",
            "COMPLETED_PLAN_STEP_INDEX": "1",
        }):
            with patch(
                "agent_script.features.build_output.build_output"
                ".post_terminal_plan_completion"
            ) as post:
                _maybe_post_plan_completion(state, "review")
                post.assert_called_once_with(_parent(index=1), 590)

    def test_terminal_signal_without_state_pointer_uses_env(self):
        """Terminal signal posts even when the state has no pointer yet.

        The pointer is reconstructed from PARENT_WORKFLOW(_STEP) env when
        ``new_state.parent`` lacks a plan index.
        """
        import os
        from workflow_state import WorkflowState
        from agent_script.features.build_output.build_output import (
            _maybe_post_plan_completion,
        )

        state = WorkflowState(
            workflow="drl",
            step="implement",
            agent="dev",
            parent=None,
        )
        with patch.dict(os.environ, {
            "WORKFLOW_STEP_TERMINAL": "true",
            "COMPLETED_PLAN_STEP_INDEX": "1",
            "PARENT_WORKFLOW": "plan-orchestration",
            "PARENT_WORKFLOW_STEP": "sub_impl",
            "PARENT_PLAN_STEP_INDEX": "2",
            "PARENT_PLAN_TOTAL_STEPS": "3",
            "TARGET_NUMBER": "590",
        }):
            with patch(
                "agent_script.features.build_output.build_output"
                ".post_terminal_plan_completion"
            ) as post:
                _maybe_post_plan_completion(state, "review")
                post.assert_called_once()
                posted, issue = post.call_args.args
                self.assertEqual(posted.plan_step_index, 1)
                self.assertEqual(posted.issue_number, 590)
                self.assertEqual(issue, 590)

    def test_pointer_without_issue_number_falls_back_to_target_number(self):
        """Fix 3: pointer lacking issue_number posts via TARGET_NUMBER env."""
        import os
        from workflow_state import WorkflowState
        from agent_script.features.build_output.build_output import (
            _maybe_post_plan_completion,
        )

        state = WorkflowState(
            workflow="dev-review-loop",
            step="review",
            agent="review",
            parent=ParentRef(
                workflow="plan-orchestration",
                step="sub_impl",
                plan_step_index=2,
            ),  # no issue_number
        )
        with patch.dict(os.environ, {"TARGET_NUMBER": "590"}):
            with patch(
                "agent_script.features.build_output.build_output"
                ".post_terminal_plan_completion"
            ) as post:
                _maybe_post_plan_completion(state, "")
                post.assert_called_once()
                posted, issue = post.call_args.args
                self.assertEqual(posted.plan_step_index, 2)
                self.assertEqual(issue, 590)

    def test_no_issue_number_resolvable_skips(self):
        """No pointer issue_number and no TARGET_NUMBER → silent no-op."""
        import os
        from workflow_state import WorkflowState
        from agent_script.features.build_output.build_output import (
            _maybe_post_plan_completion,
        )

        state = WorkflowState(
            workflow="dev-review-loop",
            step="review",
            agent="review",
            parent=ParentRef(
                workflow="plan-orchestration",
                step="sub_impl",
                plan_step_index=2,
            ),
        )
        env = {
            "COMPLETED_PLAN_STEP_INDEX": "",
            "WORKFLOW_STEP_TERMINAL": "",
            "PARENT_WORKFLOW": "",
            "PARENT_WORKFLOW_STEP": "",
            "PARENT_PLAN_STEP_INDEX": "",
            "PARENT_PLAN_TOTAL_STEPS": "",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "agent_script.features.build_output.build_output"
                ".post_terminal_plan_completion"
            ) as post:
                _maybe_post_plan_completion(state, "")
                post.assert_not_called()


class TestThreeStepPlanIntegration(unittest.TestCase):
    """Reviewer spec (issue #613, round 2): end-to-end marker sequence.

    Canonical plan-orchestration shape (3 plan steps, each mounting the same
    per-step sub-workflow): after ``step_1``'s terminal leg the posted marker
    is exactly ``<!-- bot-subtask-complete: 0 -->`` and after ``step_2``'s it
    is exactly ``<!-- bot-subtask-complete: 1 -->`` — pinning both the
    trigger (terminal return leg with non-empty WORKFLOW_NEXT) and the
    zero-indexed index. The gh boundary is faked at the subprocess level;
    resolver, trigger, and marker logic all run for real.
    """

    @staticmethod
    def _plan_fixtures(tmpdir):
        import yaml

        (Path(tmpdir) / "plan.yaml").write_text(yaml.dump({
            "name": "plan",
            "description": "3-step plan workflow",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "step_1", "workflow": "drl", "next": "step_2"},
                {"name": "step_2", "workflow": "drl", "next": "step_3"},
                {"name": "step_3", "workflow": "drl", "next": None},
            ],
        }))
        (Path(tmpdir) / "drl.yaml").write_text(yaml.dump({
            "name": "drl",
            "description": "Per-step sub-workflow",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "implement", "agent": "dev", "next": "review"},
                {"name": "review", "agent": "review", "next": None},
            ],
        }))

    def _make_fake_run(self, store: list):
        """Fake gh boundary: GET returns ``store``, POST appends to it."""

        def fake_run(cmd, **kwargs):
            if "--paginate" in cmd:
                # GET: the issue already holds the comments in ``store``
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        [{"body": b} for b in store]
                    ),
                    stderr="",
                )
            store.append(cmd[cmd.index("-f") + 1])
            return MagicMock(returncode=0, stdout="", stderr="")

        return fake_run

    def _run_terminal(self, tmpdir, parent_step: str, store: list):
        """Resolve the terminal leaf and run the real trigger once."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from resolve_workflow import resolve_workflow_routing

        result = resolve_workflow_routing(
            {
                "task": "review",
                "workflow": "drl",
                "step": "review",
                "parent_workflow": "plan",
                "parent_workflow_step": parent_step,
            },
            tmpdir, ".agents/prompts",
        )
        # State as build_output would construct it from the same resolve.
        # The parent pointer comes from the state written by the *previous*
        # run (persist-previous): for the return leg that is the refreshed
        # entry pointer; for the plan-terminates dict (no new entry) it is
        # the pointer of the step that was running — the completed step.
        plan_index = (
            result.get("PARENT_PLAN_STEP_INDEX")
            or result.get("COMPLETED_PLAN_STEP_INDEX", "")
        )
        state = WorkflowState(
            workflow=result["WORKFLOW_NAME"],
            step=result["WORKFLOW_STEP"],
            agent=result.get("ROLE_NAME", ""),
            parent=ParentRef(
                workflow="plan",
                step=result["PARENT_WORKFLOW_STEP"]
                or result["WORKFLOW_STEP"],
                issue_number=590,
                plan_step_index=int(plan_index),
                plan_total_steps=int(
                    result.get("PARENT_PLAN_TOTAL_STEPS")
                    or result.get("COMPLETED_PLAN_TOTAL_STEPS", "0")
                ),
            )
            if plan_index
            else None,
        )

        env = {
            "REPO_NAME": "o/r",
            "TARGET_NUMBER": "590",
            "WORKFLOW_STEP_TERMINAL": result.get("WORKFLOW_STEP_TERMINAL", ""),
            "COMPLETED_PLAN_STEP_INDEX": result.get(
                "COMPLETED_PLAN_STEP_INDEX", ""
            ),
            "PARENT_WORKFLOW": result.get("PARENT_WORKFLOW", ""),
            "PARENT_WORKFLOW_STEP": result.get("PARENT_WORKFLOW_STEP", ""),
            "PARENT_PLAN_STEP_INDEX": result.get("PARENT_PLAN_STEP_INDEX", ""),
            "PARENT_PLAN_TOTAL_STEPS": result.get(
                "PARENT_PLAN_TOTAL_STEPS", ""
            ),
        }
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "agent_script.features.plan_completion.plan_completion"
                ".subprocess.run",
                side_effect=self._make_fake_run(store),
            ):
                _maybe_post_plan_completion(state, result["WORKFLOW_NEXT"])

    def test_step_boundaries_post_exact_zero_indexed_markers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._plan_fixtures(tmpdir)
            store = []  # persistent comment history across both terminal legs

            # step_1 terminal → marker 0
            self._run_terminal(tmpdir, "step_1", store)
            self.assertEqual(store, ["body=<!-- bot-subtask-complete: 0 -->"])

            # step_2 terminal → marker 1 (marker 0 already on the issue)
            self._run_terminal(tmpdir, "step_2", store)
            self.assertEqual(
                store,
                [
                    "body=<!-- bot-subtask-complete: 0 -->",
                    "body=<!-- bot-subtask-complete: 1 -->",
                ],
            )

    def test_plan_terminates_posts_final_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._plan_fixtures(tmpdir)
            store = []

            # step_3 terminal → plan ends; SKIP_AGENT path uses the state
            # pointer (WORKFLOW_NEXT empty; the resolver also emits the
            # completed index, which the trigger prefers).
            self._run_terminal(tmpdir, "step_3", store)
            self.assertEqual(store, ["body=<!-- bot-subtask-complete: 2 -->"])

    def test_idempotent_skip_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._plan_fixtures(tmpdir)
            store = []

            # First pass posts marker 0...
            self._run_terminal(tmpdir, "step_1", store)
            self.assertEqual(store, ["body=<!-- bot-subtask-complete: 0 -->"])
            # ...re-running the same terminal leg posts nothing (guard).
            before = list(store)
            self._run_terminal(tmpdir, "step_1", store)
            self.assertEqual(store, before)


if __name__ == "__main__":
    unittest.main()
