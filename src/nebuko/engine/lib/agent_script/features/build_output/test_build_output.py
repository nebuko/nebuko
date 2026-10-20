"""Tests for build_output module."""

import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from build_output import (
    LATEST_ANSWER_FILE,
    _is_human_gate_hold,
    _is_recall_pass_turn,
    _parse_gate_released_env,
    _recall_aware_events,
    _resolve_pin,
    _terminal_turn_answer,
    _write_latest_answer_file,
    apply_gate_lock,
    extract_latest_answer,
    extract_standalone_answer,
    is_malformed_final_message,
    load_prev_state_or_recover,
)
from agent_script.features.run_agent.dispatch_recall import DISPATCH_RECALL_PROMPT
from workflow_state import (
    Appreciation,
    GateLock,
    GateStatus,
    HistoryEntry,
    HoldKind,
    RunPrefs,
    TurnOutcome,
    TurnStatus,
    WorkflowState,
)
from workflow_state.features.comment.to_git_comment import format_collapsed_section
from workflow_state.features.to_json.to_json import state_to_json


class TestResolvePin(unittest.TestCase):
    """Test _resolve_pin() — env override and run-prefs request (issue #617)."""

    def test_env_unset_defaults_false(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIN_COMMENT", None)
            self.assertFalse(_resolve_pin(None))

    def test_prefs_pin_true(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIN_COMMENT", None)
            self.assertTrue(_resolve_pin(RunPrefs(pin=True)))

    def test_prefs_pin_false(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIN_COMMENT", None)
            self.assertFalse(_resolve_pin(RunPrefs(pin=False)))

    def test_env_override_wins_when_prefs_absent(self):
        with mock.patch.dict(os.environ, {"PIN_COMMENT": "true"}):
            self.assertTrue(_resolve_pin(None))

    def test_env_false_does_not_override_prefs_pin(self):
        with mock.patch.dict(os.environ, {"PIN_COMMENT": "false"}):
            self.assertTrue(_resolve_pin(RunPrefs(pin=True)))

    def test_empty_env_is_false(self):
        with mock.patch.dict(os.environ, {"PIN_COMMENT": ""}):
            self.assertFalse(_resolve_pin(None))

    def test_accepted_env_truthy_values(self):
        for value in ("true", "1", "yes", "TRUE", "Yes"):
            with mock.patch.dict(os.environ, {"PIN_COMMENT": value}):
                self.assertTrue(_resolve_pin(None), msg=value)


class TestAgentPrefsReachTheResolution(unittest.TestCase):
    """Agent-supplied run prefs are wired end to end (issue #964 round 6).

    The round-6 blocker: ``action_to_prefs`` had no production caller, so
    ``dispatch_next(pin=True, complexity="high")`` was silently inert. The
    recorded knobs must reach ``_resolve_outcome`` and drive ``_resolve_pin``.
    """

    def setUp(self):
        from agent_script.features.dispatch_next_tool import reset_registry

        reset_registry()

    def test_dispatch_next_prefs_reach_resolution_and_pin(self):
        import build_output as bo
        from agent_script.features.dispatch_next_tool import (
            DispatchNextTool,
            TurnOutcomeAction,
        )

        conversation = SimpleNamespace(id="conv-prefs", run_outcome=None)
        tool = DispatchNextTool.create(conversation_id="conv-prefs")[0]
        tool.executor(
            TurnOutcomeAction(appreciation="approved", complexity="high", pin=True)
        )

        outcome, prefs, function_call = bo._resolve_outcome(conversation)
        self.assertIs(outcome.appreciation, Appreciation.APPROVED)
        self.assertIsNone(function_call)
        self.assertEqual(prefs.complexity, "high")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIN_COMMENT", None)
            self.assertTrue(_resolve_pin(prefs))

    def test_finalize_route_prefs_reach_resolution_and_pin(self):
        import build_output as bo
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="conv-finalize-prefs")[0]
        tool.executor(
            tool.action_from_arguments(
                {
                    "turn_outcome": {
                        "appreciation": "approved",
                        "complexity": "low",
                        "pin": True,
                    }
                }
            )
        )

        conversation = SimpleNamespace(id="conv-finalize-prefs", run_outcome=None)
        outcome, prefs, _ = bo._resolve_outcome(conversation)
        self.assertIs(outcome.appreciation, Appreciation.APPROVED)
        self.assertEqual(prefs.complexity, "low")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIN_COMMENT", None)
            self.assertTrue(_resolve_pin(prefs))


class TestIsMalformedFinalMessage(unittest.TestCase):
    """Test the is_malformed_final_message() function."""

    def test_empty_string_is_not_malformed(self):
        """Empty string is not malformed (handled by 'not final_message')."""
        self.assertFalse(is_malformed_final_message(""))

    def test_normal_text_is_not_malformed(self):
        """Plain text without any tags is not malformed."""
        self.assertFalse(is_malformed_final_message("Hello world"))

    def test_balanced_antthinking_is_not_malformed(self):
        """Balanced antThinking tags are not malformed."""
        msg = "<antThinking>some thinking</antThinking>"
        self.assertFalse(is_malformed_final_message(msg))

    def test_unclosed_antthinking_is_malformed(self):
        """Unclosed antThinking tag is malformed."""
        msg = "<antThinking>some thinking"
        self.assertTrue(is_malformed_final_message(msg))

    def test_unclosed_thinking_is_malformed(self):
        """Unclosed thinking tag is malformed."""
        msg = "<thinking>some thinking"
        self.assertTrue(is_malformed_final_message(msg))

    def test_balanced_tool_block_is_not_malformed(self):
        """Balanced tool block markers are not malformed."""
        msg = "Text before\n<tool_call>tool_call\nsome params\n</tool_call> after"
        self.assertFalse(is_malformed_final_message(msg))

    def test_unclosed_tool_block_is_malformed(self):
        """Tool block opened but not closed is malformed."""
        msg = "Some text\n<tool_call>\nfunction=terminal\nparam=value"
        self.assertTrue(is_malformed_final_message(msg))

    def test_text_with_unclosed_tool_block(self):
        """Normal text followed by mid-block truncation is malformed."""
        msg = "Here is my answer\n<tool_call>\nmore text here"
        self.assertTrue(is_malformed_final_message(msg))

    def test_multiple_balanced_blocks_are_not_malformed(self):
        """Multiple balanced blocks across different tag types are fine."""
        msg = "<antThinking>thought</antThinking> some text <tool_call>tool\n</tool_call> done"
        self.assertFalse(is_malformed_final_message(msg))

    def test_multiple_unclosed_blocks_are_malformed(self):
        """Any imbalance across multiple blocks is malformed."""
        msg = "<antThinking>thought</antThinking> <tool_call>unclosed\nsecond"
        self.assertTrue(is_malformed_final_message(msg))

    def test_only_closing_tag_is_malformed(self):
        """A closing tag without a matching opener is also malformed."""
        msg = "some text</antThinking>"
        self.assertTrue(is_malformed_final_message(msg))

    def test_balanced_tool_code_is_not_malformed(self):
        """Balanced tool_code tags are not malformed."""
        msg = "<tool_code>code here</tool_code>"
        self.assertFalse(is_malformed_final_message(msg))

    def test_unclosed_tool_code_is_malformed(self):
        """Unclosed tool_code tag is malformed."""
        msg = "<tool_code>code here"
        self.assertTrue(is_malformed_final_message(msg))


def _completed(appreciation="approved", **kwargs) -> TurnOutcome:
    """A completing turn with a verdict - the shape the gate may intercept."""
    return TurnOutcome(
        status=TurnStatus.COMPLETED, appreciation=appreciation, **kwargs
    )


def _gate_hold(expected_step: str) -> TurnOutcome:
    """A legacy gate hold, as persisted before #1033 (read-compat only)."""
    return TurnOutcome(
        status=TurnStatus.COMPLETED,
        appreciation=Appreciation.HOLD,
        hold_kind=HoldKind.AWAITING_INPUT,
        note=f"human_gate:{expected_step}",
    )


class TestHumanGateIntercept(unittest.TestCase):
    """Test apply_gate_lock() - issues #506/#1033 human gate boundary."""

    def _write_workflow(self, tmpdir, name, steps_yaml):
        path = os.path.join(tmpdir, f"{name}.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"name: {name}\n"
                f'description: "test workflow"\n'
                f"steps:\n{steps_yaml}"
            )
        return path

    def test_gate_step_advance_non_resume_arms_the_lock(self):
        """A completing turn on a human_gate step arms the lock, verdict intact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            outcome = _completed()
            with mock.patch.dict(os.environ, env, clear=False):
                result, gate = apply_gate_lock(outcome, "wf", "implement")
        self.assertEqual(result, outcome)
        self.assertEqual(gate.step, "implement")
        self.assertTrue(gate.armed())

    def test_gate_step_advance_resume_still_arms_the_lock(self):
        """Issue #648: even a /nebuko-resume-triggered run arms the lock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir, "HUMAN_RESUME_INPUT": "/nebuko resume"}
            outcome = _completed()
            with mock.patch.dict(os.environ, env, clear=False):
                result, gate = apply_gate_lock(outcome, "wf", "implement")
        self.assertEqual(result, outcome)
        self.assertTrue(gate.armed())

    def test_non_gate_step_advance_unchanged(self):
        """A step without human_gate keeps the agent's verdict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    next: implement\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            outcome = _completed()
            with mock.patch.dict(os.environ, env, clear=False):
                result, gate = apply_gate_lock(outcome, "wf", "plan")
        self.assertEqual(result, outcome)
        self.assertIsNone(gate)

    def test_gate_step_soft_kill_non_resume_keeps_retrying(self):
        """An INCOMPLETE turn is never intercepted: the gate only discards a
        completing verdict, so a soft kill keeps its retry semantics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            soft_kill = TurnOutcome(
                status=TurnStatus.INCOMPLETE,
                incomplete_reason="soft_timeout",
            )
            with mock.patch.dict(os.environ, env, clear=False):
                result, _ = apply_gate_lock(soft_kill, "wf", "implement")
        self.assertEqual(result, soft_kill)

    def test_gate_step_soft_kill_resume_keeps_retrying(self):
        """Issue #648: a resume-triggered soft kill on a gated step still retries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir, "HUMAN_RESUME_INPUT": "/nebuko resume"}
            soft_kill = TurnOutcome(
                status=TurnStatus.INCOMPLETE,
                incomplete_reason="soft_timeout",
            )
            with mock.patch.dict(os.environ, env, clear=False):
                result, _ = apply_gate_lock(soft_kill, "wf", "implement")
        self.assertEqual(result, soft_kill)

    def test_gate_step_verdict_is_never_rewritten(self):
        """Issue #1033: the agent's verdict survives; only the lock blocks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            with mock.patch.dict(os.environ, env, clear=False):
                result, gate = apply_gate_lock(
                    _completed("rejected", round=3), "wf", "implement"
                )
        self.assertIs(result.appreciation, Appreciation.REJECTED)
        self.assertNotEqual(result, _gate_hold("implement"))
        self.assertTrue(gate.armed())

    def test_non_gate_step_verdict_preserved(self):
        """A verdict on a non-gated step is left untouched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    next: implement\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            outcome = _completed("rejected", round=2)
            with mock.patch.dict(os.environ, env, clear=False):
                result, _ = apply_gate_lock(outcome, "wf", "plan")
        self.assertEqual(result, outcome)
        self.assertIs(result.appreciation, Appreciation.REJECTED)

    def test_incomplete_turn_is_never_intercepted(self):
        """An interrupted turn is never turned into a gate hold (issue #704)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            incomplete = TurnOutcome(
                status=TurnStatus.INCOMPLETE,
                incomplete_reason="interrupted",
            )
            with mock.patch.dict(os.environ, env, clear=False):
                result, _ = apply_gate_lock(incomplete, "wf", "implement")
        self.assertEqual(result, incomplete)

    def test_missing_outcome_on_gate_step_still_arms_the_lock(self):
        """No outcome: nothing to rewrite, but the gated step is still locked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            with mock.patch.dict(os.environ, env, clear=False):
                result, gate = apply_gate_lock(None, "wf", "implement")
        self.assertIsNone(result)
        self.assertTrue(gate.armed())

    def test_missing_workflow_name_never_crashes(self):
        """Missing workflow name degrades to no-op (no crash, outcome unchanged)."""
        outcome = _completed()
        result, gate = apply_gate_lock(outcome, "", "implement")
        self.assertEqual(result, outcome)
        self.assertIsNone(gate)

    def test_missing_workflow_file_degrades_to_no_op(self):
        """A missing/invalid workflow file must not crash and leaves it intact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"WORKFLOWS_DIR": tmpdir}
            outcome = _completed()
            with mock.patch.dict(os.environ, env, clear=False):
                result, _ = apply_gate_lock(outcome, "does-not-exist", "implement")
        self.assertEqual(result, outcome)


class TestGateLockArming(unittest.TestCase):
    """The intercept arms a root-level ``GateLock`` (issue #1003).

    The lock is the single source of truth for "the workflow may not advance".
    It is independent of the turn outcome and never edits the hand-off, so an
    agent parked on a gated step can still delegate (``call_agent``).
    """

    def _write_workflow(self, tmpdir, name, steps_yaml):
        path = os.path.join(tmpdir, f"{name}.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"name: {name}\n"
                f'description: "test workflow"\n'
                f"steps:\n{steps_yaml}"
            )
        return path

    def test_intercept_arms_the_lock_at_the_gated_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    human_gate: true\n    next: implement\n",
            )
            with mock.patch.dict(os.environ, {"WORKFLOWS_DIR": tmpdir}, clear=False):
                _, gate = apply_gate_lock(_completed(), "wf", "plan")
        self.assertIsNotNone(gate)
        self.assertEqual(gate.step, "plan")
        self.assertTrue(gate.armed())
        self.assertEqual(gate.consult_rounds, 0)

    def test_intercept_carries_a_prior_lock_forward(self):
        prior = GateLock(step="plan", consult_rounds=2, last_handoff="context")
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    human_gate: true\n    next: implement\n",
            )
            with mock.patch.dict(os.environ, {"WORKFLOWS_DIR": tmpdir}, clear=False):
                _, gate = apply_gate_lock(
                    _completed(), "wf", "plan", gate=prior
                )
        self.assertEqual(gate, prior)

    def test_intercept_arms_a_gate_that_was_released(self):
        released = GateLock(step="plan", status=GateStatus.RELEASED)
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    human_gate: true\n    next: implement\n",
            )
            with mock.patch.dict(os.environ, {"WORKFLOWS_DIR": tmpdir}, clear=False):
                _, gate = apply_gate_lock(
                    _completed(), "wf", "plan", gate=released
                )
        self.assertTrue(gate.armed())

    def test_non_gated_step_arms_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    next: implement\n",
            )
            with mock.patch.dict(os.environ, {"WORKFLOWS_DIR": tmpdir}, clear=False):
                _, gate = apply_gate_lock(_completed(), "wf", "plan")
        self.assertIsNone(gate)

    def test_release_env_marks_the_lock_released(self):
        """The gate-decision run's own persist is released, not re-armed.

        The release is applied by ``main`` *after* the intercept (which always
        re-arms a gated step), so this exercises the same two steps in order.
        """
        prior = GateLock(step="plan")
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    human_gate: true\n    next: implement\n",
            )
            env = {
                "WORKFLOWS_DIR": tmpdir,
                "GATE_DECISION": json.dumps(
                    {
                        "status": "approved",
                        "workflow": "wf",
                        "target_step": "plan",
                        "decided_by": "olivier",
                        "decided_at": "2026-10-18T10:00:00+00:00",
                    }
                ),
                "GATE_RELEASED": "true",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                _, gate = apply_gate_lock(
                    _completed(), "wf", "plan", gate=prior
                )
                # The call site's release step (``build_output.main``).
                self.assertTrue(_parse_gate_released_env())
                if _parse_gate_released_env() and gate is not None:
                    gate = gate.model_copy(update={"status": GateStatus.RELEASED})
        self.assertIsNotNone(gate)
        self.assertFalse(gate.armed())
        self.assertIs(gate.status, GateStatus.RELEASED)

    def test_release_is_ignored_when_no_lock_is_armed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    human_gate: true\n    next: implement\n",
            )
            with mock.patch.dict(
                os.environ,
                {"WORKFLOWS_DIR": tmpdir, "GATE_RELEASED": "true"},
                clear=False,
            ):
                _, gate = apply_gate_lock(_completed(), "wf", "plan")
        self.assertTrue(gate.armed())

    def test_consult_env_spends_one_round_on_the_persisted_lock(self):
        """Issue #1003 review: ``consume_consult`` has a production caller.

        The delegated consult's run is started with ``GATE_CONSULT=<callee>``
        (set by the consult resolution in ``resolve_config``). The lock carried
        by that run's ``build_state`` must have spent exactly one consult round,
        which is what bounds the gate fan-out at ``MAX_CONSULT_ROUNDS``.
        """
        import build_output as bo
        from types import SimpleNamespace as NS
        from workflow_state import GateLock, RunPrefs, WorkflowState

        prior = WorkflowState(
            workflow="wf",
            step="plan",
            agent="architect",
            gate=GateLock(step="plan"),
        )
        captured = {}

        def _capture_build_state(prev_state, **kwargs):
            captured.update(kwargs)
            return NS(run_id=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    human_gate: true\n    next: implement\n",
            )
            env = {
                "WORKFLOWS_DIR": tmpdir,
                "WORKFLOW_NAME": "wf",
                "WORKFLOW_STEP": "review",
                "ROLE_NAME": "context",
                "GATE_CONSULT": "context",
            }
            conversation = NS(
                state=NS(events=[_msg_event("callee answer")]),
                id="conv-1003-consult",
                run_outcome=None,
            )
            metrics = NS(review_fix_requests=0, to_dict=lambda: {})
            with mock.patch.object(bo, "get_agent_final_response", return_value="callee answer"), \
                 mock.patch.object(bo, "_load_prev_state", return_value=prior), \
                 mock.patch.object(bo, "_build_issue_info", return_value=None), \
                 mock.patch.object(bo, "_build_pr_info", return_value=None), \
                 mock.patch.object(
                     bo, "_resolve_outcome",
                     return_value=(_completed(), RunPrefs(), None),
                 ), \
                 mock.patch.object(bo, "clear_run"), \
                 mock.patch.object(bo, "clear_call_agent_run"), \
                 mock.patch.object(bo, "clear_scaffold_branch_run"), \
                 mock.patch.object(bo, "clear_complexity_review_run"), \
                 mock.patch.object(bo, "get_recorded_review_request", return_value=None), \
                 mock.patch.object(bo, "build_state", side_effect=_capture_build_state), \
                 mock.patch.object(bo, "split_message", return_value=[]), \
                 mock.patch.object(bo, "_write_output_files", return_value=None), \
                 mock.patch.dict(os.environ, env, clear=False):
                cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    bo.build_output(conversation, metrics)
                finally:
                    os.chdir(cwd)

        gate = captured["gate"]
        self.assertIsNotNone(gate)
        self.assertEqual(gate.consult_rounds, 1)
        self.assertEqual(gate.last_handoff, "context")
        self.assertTrue(gate.armed())

    def test_no_consult_env_leaves_the_lock_round_untouched(self):
        """A non-consult run must not spend a round (issue #1003 review)."""
        import build_output as bo
        from types import SimpleNamespace as NS
        from workflow_state import GateLock, RunPrefs, WorkflowState

        prior = WorkflowState(
            workflow="wf",
            step="plan",
            agent="architect",
            gate=GateLock(step="plan", consult_rounds=1, last_handoff="context"),
        )
        captured = {}

        def _capture_build_state(prev_state, **kwargs):
            captured.update(kwargs)
            return NS(run_id=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: architect\n    human_gate: true\n    next: implement\n",
            )
            env = {
                "WORKFLOWS_DIR": tmpdir,
                "WORKFLOW_NAME": "wf",
                "WORKFLOW_STEP": "plan",
                "ROLE_NAME": "architect",
            }
            conversation = NS(
                state=NS(events=[_msg_event("plan answer")]),
                id="conv-1003-no-consult",
                run_outcome=None,
            )
            metrics = NS(review_fix_requests=0, to_dict=lambda: {})
            with mock.patch.object(bo, "get_agent_final_response", return_value="plan answer"), \
                 mock.patch.object(bo, "_load_prev_state", return_value=prior), \
                 mock.patch.object(bo, "_build_issue_info", return_value=None), \
                 mock.patch.object(bo, "_build_pr_info", return_value=None), \
                 mock.patch.object(
                     bo, "_resolve_outcome",
                     return_value=(_completed(), RunPrefs(), None),
                 ), \
                 mock.patch.object(bo, "clear_run"), \
                 mock.patch.object(bo, "clear_call_agent_run"), \
                 mock.patch.object(bo, "clear_scaffold_branch_run"), \
                 mock.patch.object(bo, "clear_complexity_review_run"), \
                 mock.patch.object(bo, "get_recorded_review_request", return_value=None), \
                 mock.patch.object(bo, "build_state", side_effect=_capture_build_state), \
                 mock.patch.object(bo, "split_message", return_value=[]), \
                 mock.patch.object(bo, "_write_output_files", return_value=None), \
                 mock.patch.dict(os.environ, env, clear=True):
                cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    bo.build_output(conversation, metrics)
                finally:
                    os.chdir(cwd)

        gate = captured["gate"]
        self.assertIsNotNone(gate)
        self.assertEqual(gate.consult_rounds, 1)


class TestIsHumanGateHold(unittest.TestCase):
    """Issue #887: the shape detector that keeps persisted state and router aligned."""

    def test_gate_hold_detected(self):
        self.assertTrue(_is_human_gate_hold(_gate_hold("plan")))

    def test_plain_hold_not_detected(self):
        self.assertFalse(
            _is_human_gate_hold(
                TurnOutcome(
                    status=TurnStatus.COMPLETED,
                    appreciation=Appreciation.HOLD,
                    hold_kind=HoldKind.AWAITING_INPUT,
                    note="no-action default",
                )
            )
        )

    def test_completed_without_hold_not_detected(self):
        self.assertFalse(_is_human_gate_hold(_completed(note="human_gate:plan")))

    def test_none_not_detected(self):
        self.assertFalse(_is_human_gate_hold(None))

    def test_gate_intercept_at_call_site_clears_resolution_next_step(self):
        """Issue #887: when the intercept holds a gated step, the persisted
        resolution must not keep advertising the ungated forward edge."""
        from types import SimpleNamespace as NS

        import build_output as bo

        captured = {}

        def _capture_build_state(prev_state, **kwargs):
            captured.update(kwargs)
            return NS(run_id=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "gated.yaml"), "w") as f:
                f.write(
                    "name: gated\n"
                    "description: test\n"
                    "trigger:\n  agents: [architect]\n"
                    "steps:\n"
                    "  - name: plan\n    agent: architect\n"
                    "    next: implement\n    human_gate: true\n"
                    "  - name: implement\n    agent: dev\n    next: review\n"
                )
            env = {
                "WORKFLOWS_DIR": tmpdir,
                "WORKFLOW_NAME": "gated",
                "WORKFLOW_STEP": "plan",
                "WORKFLOW_NEXT": "implement",
                "ROLE_NAME": "architect",
            }
            conversation = NS(
                state=NS(events=[_msg_event("plan answer")]),
                id="conv-887",
                run_outcome=None,
            )
            metrics = NS(review_fix_requests=0, to_dict=lambda: {})
            with mock.patch.object(bo, "get_agent_final_response", return_value="plan answer"), \
                 mock.patch.object(bo, "_load_prev_state", return_value=None), \
                 mock.patch.object(bo, "_build_issue_info", return_value=None), \
                 mock.patch.object(bo, "_build_pr_info", return_value=None), \
                 mock.patch.object(
                     bo, "_resolve_outcome",
                     return_value=(_completed(), RunPrefs(), None),
                 ), \
                 mock.patch.object(bo, "clear_run"), \
                 mock.patch.object(bo, "clear_scaffold_branch_run"), \
                 mock.patch.object(bo, "clear_complexity_review_run"), \
                 mock.patch.object(bo, "get_recorded_review_request", return_value=None), \
                 mock.patch.object(bo, "build_state", side_effect=_capture_build_state), \
                 mock.patch.object(bo, "split_message", return_value=[]), \
                 mock.patch.object(bo, "_write_output_files", return_value=None), \
                 mock.patch.dict(os.environ, env, clear=False):
                cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    bo.build_output(conversation, metrics)
                finally:
                    os.chdir(cwd)

        self.assertEqual(captured["resolution"].next_step, "")

    def test_gate_intercept_at_call_site_preserves_function_call_and_arms_gate(self):
        """Issue #1003: the intercept raises the root gate but must NOT drop the
        agent's ``call_agent`` hand-off — the outcome and the delegation are
        orthogonal, so a gated agent may still consult another agent."""
        from types import SimpleNamespace as NS

        import build_output as bo
        from workflow_state import FunctionCall

        captured = {}

        def _capture_build_state(prev_state, **kwargs):
            captured.update(kwargs)
            return NS(run_id=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "gated.yaml"), "w") as f:
                f.write(
                    "name: gated\n"
                    "description: test\n"
                    "trigger:\n  agents: [architect]\n"
                    "steps:\n"
                    "  - name: plan\n    agent: architect\n"
                    "    next: implement\n    human_gate: true\n"
                    "  - name: implement\n    agent: dev\n    next: review\n"
                )
            env = {
                "WORKFLOWS_DIR": tmpdir,
                "WORKFLOW_NAME": "gated",
                "WORKFLOW_STEP": "plan",
                "WORKFLOW_NEXT": "implement",
                "ROLE_NAME": "architect",
            }
            conversation = NS(
                state=NS(events=[_msg_event("plan answer")]),
                id="conv-887-fc",
                run_outcome=None,
            )
            metrics = NS(review_fix_requests=0, to_dict=lambda: {})
            with mock.patch.object(bo, "get_agent_final_response", return_value="plan answer"), \
                 mock.patch.object(bo, "_load_prev_state", return_value=None), \
                 mock.patch.object(bo, "_build_issue_info", return_value=None), \
                 mock.patch.object(bo, "_build_pr_info", return_value=None), \
                 mock.patch.object(
                     bo, "_resolve_outcome",
                     return_value=(
                         _completed(),
                         RunPrefs(),
                         FunctionCall(function="call_agent", agent_name="dev"),
                     ),
                 ), \
                 mock.patch.object(bo, "clear_run"), \
                 mock.patch.object(bo, "clear_scaffold_branch_run"), \
                 mock.patch.object(bo, "clear_complexity_review_run"), \
                 mock.patch.object(bo, "get_recorded_review_request", return_value=None), \
                 mock.patch.object(bo, "build_state", side_effect=_capture_build_state), \
                 mock.patch.object(bo, "split_message", return_value=[]), \
                 mock.patch.object(bo, "_write_output_files", return_value=None), \
                 mock.patch.dict(os.environ, env, clear=False):
                cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    bo.build_output(conversation, metrics)
                finally:
                    os.chdir(cwd)

        self.assertIs(captured["turn_outcome"].appreciation, Appreciation.APPROVED)
        # The hand-off survives the intercept (issue #1003) …
        self.assertIsNotNone(captured["function_call"])
        self.assertEqual(captured["function_call"].agent_name, "dev")
        # … and the root gate is armed for the gated step.
        self.assertIsNotNone(captured["gate"])
        self.assertEqual(captured["gate"].step, "plan")
        self.assertTrue(captured["gate"].armed())


class TestHumanGateLayeredWorkflowLookup(unittest.TestCase):
    """The gate check resolves workflows per file, not per directory (#987).

    ``config_dir("workflows")`` returns the first existing *directory*, so a
    repo-local ``.nebuko/workflows/`` used to hide the shipped
    ``defaults/workflows/`` and silently disable every human gate.
    """

    def setUp(self):
        self._cwd = os.getcwd()
        self._repo = tempfile.mkdtemp()
        os.chdir(self._repo)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._repo, ignore_errors=True)

    def _write_workflow(self, directory, name, steps_yaml):
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{name}.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"name: {name}\n"
                f'description: "test workflow"\n'
                f"steps:\n{steps_yaml}"
            )
        return path

    def test_local_workflow_does_not_hide_the_shipped_gated_step(self):
        """Repro of #987: one local workflow must not disable the shipped gate."""
        self._write_workflow(
            os.path.join(self._repo, ".nebuko", "workflows"),
            "my-custom",
            "  - name: only\n    agent: dev\n    next: null\n",
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORKFLOWS_DIR", None)
            result, gate = apply_gate_lock(_completed(), "default", "plan")
        self.assertIs(result.appreciation, Appreciation.APPROVED)
        self.assertEqual(gate.step, "plan")
        self.assertTrue(gate.armed())

    def test_local_workflow_does_not_hide_a_pinned_shipped_dir(self):
        """A local override dir must not hide the WORKFLOWS_DIR layer either."""
        shipped = os.path.join(self._repo, "shipped")
        self._write_workflow(
            shipped,
            "gated",
            "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
        )
        self._write_workflow(
            os.path.join(self._repo, ".nebuko", "workflows"),
            "other",
            "  - name: only\n    agent: dev\n    next: null\n",
        )
        with mock.patch.dict(os.environ, {"WORKFLOWS_DIR": shipped}, clear=False):
            result, gate = apply_gate_lock(_completed(), "gated", "implement")
        self.assertIs(result.appreciation, Appreciation.APPROVED)
        self.assertEqual(gate.step, "implement")

    def test_local_override_still_wins_over_the_shipped_file(self):
        """Issue #529 precedence is preserved: the local file is the one read."""
        self._write_workflow(
            os.path.join(self._repo, "shipped"),
            "gated",
            "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
        )
        self._write_workflow(
            os.path.join(self._repo, ".nebuko", "workflows"),
            "gated",
            "  - name: implement\n    agent: dev\n    next: review\n",
        )
        outcome = _completed()
        with mock.patch.dict(
            os.environ,
            {"WORKFLOWS_DIR": os.path.join(self._repo, "shipped")},
            clear=False,
        ):
            result, _ = apply_gate_lock(outcome, "gated", "implement")
        self.assertEqual(result, outcome)

    def test_declared_name_only_in_local_dir_arms_the_gate(self):
        """Repro of #1034: WORKFLOW_NAME is the declared name, not the file stem.

        The run executes ``.nebuko/workflows/default-with-output.yaml`` (declared
        ``name: default-with-output``), which has no stem match anywhere, so a
        stem-only lookup warned "could not load workflow" and the gate never armed.
        """
        shipped = os.path.join(self._repo, "shipped")
        self._write_workflow(
            shipped,
            "default",
            "  - name: plan\n    agent: architect\n    next: null\n",
        )
        os.makedirs(os.path.join(self._repo, ".nebuko", "workflows"), exist_ok=True)
        with open(
            os.path.join(self._repo, ".nebuko", "workflows", "default-with-output.yaml"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "name: default-with-output\n"
                'description: "test workflow"\n'
                "steps:\n"
                "  - name: plan\n"
                "    agent: architect\n"
                "    human_gate: true\n"
                "    next: null\n"
            )
        with mock.patch.dict(os.environ, {"WORKFLOWS_DIR": shipped}, clear=False):
            with self.assertNoLogs(
                "build_output", level="WARNING"
            ):
                result, gate = apply_gate_lock(
                    _completed(), "default-with-output", "plan"
                )
        self.assertIs(result.appreciation, Appreciation.APPROVED)
        self.assertEqual(gate.step, "plan")
        self.assertTrue(gate.armed())

    def test_declared_name_differing_from_filename_arms_the_gate(self):
        """A local file whose stem differs from its declared name still resolves."""
        self._write_workflow(
            os.path.join(self._repo, "shipped"),
            "default",
            "  - name: plan\n    agent: architect\n    next: null\n",
        )
        os.makedirs(os.path.join(self._repo, ".nebuko", "workflows"), exist_ok=True)
        with open(
            os.path.join(self._repo, ".nebuko", "workflows", "custom.yaml"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "name: default-with-output\n"
                'description: "test workflow"\n'
                "steps:\n"
                "  - name: plan\n"
                "    agent: architect\n"
                "    human_gate: true\n"
                "    next: null\n"
            )
        with mock.patch.dict(
            os.environ, {"WORKFLOWS_DIR": os.path.join(self._repo, "shipped")}, clear=False
        ):
            result, gate = apply_gate_lock(_completed(), "default-with-output", "plan")
        self.assertIs(result.appreciation, Appreciation.APPROVED)
        self.assertTrue(gate.armed())

    def test_unknown_workflow_stays_fail_soft(self):
        """No stem and no declared name: warn and degrade to auto-advance."""
        self._write_workflow(
            os.path.join(self._repo, "shipped"),
            "default",
            "  - name: plan\n    agent: architect\n    next: null\n",
        )
        outcome = _completed()
        with mock.patch.dict(
            os.environ, {"WORKFLOWS_DIR": os.path.join(self._repo, "shipped")}, clear=False
        ):
            with self.assertLogs(
                "build_output", level="WARNING"
            ) as logs:
                result, gate = apply_gate_lock(outcome, "does-not-exist", "plan")
        self.assertEqual(result, outcome)
        self.assertIsNone(gate)
        self.assertIn("could not load workflow 'does-not-exist'", logs.output[0])


class TestHumanGateRealWorkflowYaml(unittest.TestCase):
    """Regression against the real workflows/spec-driven-impl.yaml (issue #506).

    Ensures the shipped YAML still parses (no validator conflict on the
    next+conditions combo), stub_approval is gated, implement is not, and
    that the load-bearing 'next: implement' fallback keeps routing alive.
    """

    @staticmethod
    def _real_workflows_dir():
        # The shipped definitions, not the per-repo ``.nebuko`` override:
        # ``config_dir`` prefers a checkout-local override directory whose
        # contents vary per machine, so pin the packaged defaults.
        from default_paths import checkout_root
        return str(checkout_root() / "defaults" / "workflows")

    def setUp(self):
        self.env = {"WORKFLOWS_DIR": self._real_workflows_dir()}

    def test_real_yaml_gated_step_intercepts_advance(self):
        with mock.patch.dict(os.environ, self.env, clear=False):
            result, gate = apply_gate_lock(
                _completed(), "spec-driven-impl", "stub_approval"
            )
        self.assertIs(result.appreciation, Appreciation.APPROVED)
        self.assertEqual(gate.step, "stub_approval")
        self.assertTrue(gate.armed())

    def test_real_yaml_gated_step_resume_held(self):
        """Issue #648: no resume bypass - the real gated step always holds."""
        with mock.patch.dict(os.environ, self.env, clear=False):
            result, gate = apply_gate_lock(
                _completed(), "spec-driven-impl", "stub_approval"
            )
        self.assertTrue(gate.armed())

    def test_real_yaml_non_gated_step_unchanged(self):
        outcome = _completed()
        with mock.patch.dict(os.environ, self.env, clear=False):
            result, _ = apply_gate_lock(
                outcome, "spec-driven-impl", "implement"
            )
        self.assertEqual(result, outcome)

    def test_real_yaml_verdict_from_gated_step_routes_on(self):
        """A plain advance run from stub_approval is intercepted to a hold, but
        resolving its downstream routing lands on the implement step's agent -
        proving 'next: implement' is load-bearing."""
        from resolve_config import resolve_config
        import json
        prompt_base = tempfile.mkdtemp()
        for role in ("dev", "architect", "context", "review"):
            os.makedirs(os.path.join(prompt_base, role), exist_ok=True)
            with open(os.path.join(prompt_base, role, "system.md"), "w") as f:
                f.write(f"# {role}")
        state = {
            "version": 6, "workflow": "spec-driven-impl", "step": "stub_approval",
            "agent": "architect", "phase": "stub_approval", "history": [],
            "turn_outcome": {"status": "completed", "appreciation": "approved"},
        }
        body = "<!-- bot-agent-architect -->\n*Architect answer*\n\n```json\n" \
               "<!-- WORKFLOW_STATE_START -->\n" + json.dumps(state) + \
               "\n<!-- WORKFLOW_STATE_END -->\n```"
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = resolve_config(body, prompt_base, self._real_workflows_dir(), is_bot=True)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["WORKFLOW_STEP"], "implement")
        self.assertEqual(result["SKIP_AGENT"], "false")


def _msg_event(text: str) -> "object":
    """Build a minimal assistant MessageEvent for capture-layer tests."""
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message

    return MessageEvent(llm_message=Message(role="assistant", content=text), source="agent")


def _user_event(text: str) -> "object":
    """Build a user MessageEvent (must never be captured as an answer)."""
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message

    return MessageEvent(llm_message=Message(role="user", content=text), source="user")


def _finish_action_event(text: str) -> "object":
    """Build the finish-turn ActionEvent the SDK emits when answer + finish share a turn.

    The SDK stores the turn's text in ``ActionEvent.thought`` and does NOT emit
    a standalone ``MessageEvent`` (the issue #553 regression path).
    """
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    tool_call = MessageToolCall(
        id="tc-finish", name="finish", arguments="{}", origin="completion"
    )
    return ActionEvent(
        source="agent",
        tool_name="finish",
        thought=[TextContent(text=text)],
        tool_call_id="tc-finish",
        tool_call=tool_call,
        llm_response_id="r1",
    )


def _finish_event_with_thought(thought: str, message: str) -> "object":
    """Build a finish-turn ActionEvent carrying both turn text and a typed message.

    This is the issue #822 shape: the model wrote a one-line lead-in as the turn
    text and put the full report in the ``finish`` ``message`` argument.
    """
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent
    from openhands.sdk.tool.builtins.finish import FinishAction

    tool_call = MessageToolCall(
        id="tc-finish-report", name="finish", arguments="{}", origin="completion"
    )
    return ActionEvent(
        source="agent",
        tool_name="finish",
        thought=[TextContent(text=thought)],
        tool_call_id="tc-finish-report",
        tool_call=tool_call,
        llm_response_id="r-report",
        action=FinishAction(message=message),
    )


def _dispatch_action_event(text: str) -> "object":
    """Build a dispatch_next-turn ActionEvent carrying the deliverable in thought.

    The full report is emitted on the same turn as the mandatory pre-finish
    ``dispatch_next`` call, with no standalone ``MessageEvent`` (the issue
    #555 regression path). The action arguments hold routing metadata only.
    """
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    tool_call = MessageToolCall(
        id="tc-dispatch",
        name="dispatch_next",
        arguments='{"status": "advance"}',
        origin="completion",
    )
    return ActionEvent(
        source="agent",
        tool_name="dispatch_next",
        thought=[TextContent(text=text)],
        tool_call_id="tc-dispatch",
        tool_call=tool_call,
        llm_response_id="r1",
    )


class TestExtractLatestAnswer(unittest.TestCase):
    """Capture layer: latest-wins selection of the agent's last text answer (issue #522)."""

    def test_empty_events_returns_empty(self):
        self.assertEqual(extract_latest_answer([]), "")
        self.assertEqual(extract_latest_answer(None), "")

    def test_no_text_answers_returns_empty(self):
        events = [_user_event("human text only")]
        self.assertEqual(extract_latest_answer(events), "")

    def test_single_answer_captured(self):
        events = [_msg_event("  Full research report  ")]
        self.assertEqual(extract_latest_answer(events), "Full research report")

    def test_multiple_answers_last_wins(self):
        events = [_msg_event("earlier answer"), _msg_event("latest answer")]
        self.assertEqual(extract_latest_answer(events), "latest answer")

    def test_blank_answer_does_not_replace_previous(self):
        events = [_msg_event("kept answer"), _msg_event("   \n  ")]
        self.assertEqual(extract_latest_answer(events), "kept answer")

    def test_user_messages_are_ignored(self):
        events = [_msg_event("agent answer"), _user_event("user interjection")]
        self.assertEqual(extract_latest_answer(events), "agent answer")

    # --- issue #553 regression: finish-turn text lives in ActionEvent.thought ---

    def test_finish_turn_thought_captured_when_action_absent(self):
        """A finish call with no action payload falls back to the turn text."""
        events = [_finish_action_event("  Full research report  ")]
        self.assertEqual(extract_latest_answer(events), "Full research report")

    def test_finish_turn_replaces_earlier_message(self):
        events = [_msg_event("earlier answer"), _finish_action_event("finish report")]
        self.assertEqual(extract_latest_answer(events), "finish report")

    def test_newer_message_after_finish_turn_wins(self):
        events = [_finish_action_event("finish report"), _msg_event("newer answer")]
        self.assertEqual(extract_latest_answer(events), "newer answer")

    def test_non_finish_action_event_is_ignored(self):
        """Mid-run commentary on other tool calls must not be captured."""
        from openhands.sdk.event import ActionEvent
        from openhands.sdk.llm.message import MessageToolCall, TextContent

        tc = MessageToolCall(id="tc-1", name="terminal", arguments="{}", origin="completion")
        event = ActionEvent(
            source="agent",
            tool_name="terminal",
            thought=[TextContent(text="mid-run commentary")],
            tool_call_id="tc-1",
            tool_call=tc,
            llm_response_id="r2",
        )
        self.assertEqual(extract_latest_answer([event]), "")

    def test_blank_finish_turn_thought_returns_empty(self):
        events = [_finish_action_event("   \n  ")]
        self.assertEqual(extract_latest_answer(events), "")

    # --- issue #555 regression: deliverable lives on the dispatch_next turn ---

    def test_dispatch_next_turn_thought_captured_when_no_message_event(self):
        """Full report + dispatch_next sharing one turn is captured from thought."""
        events = [_dispatch_action_event("  Full context report  ")]
        self.assertEqual(extract_latest_answer(events), "Full context report")

    def test_dispatch_next_turn_replaces_earlier_message(self):
        events = [_msg_event("earlier answer"), _dispatch_action_event("dispatch report")]
        self.assertEqual(extract_latest_answer(events), "dispatch report")

    def test_finish_turn_after_dispatch_turn_wins(self):
        events = [_dispatch_action_event("dispatch report"), _finish_action_event("finish report")]
        self.assertEqual(extract_latest_answer(events), "finish report")

    def test_newer_message_after_dispatch_turn_wins(self):
        events = [_dispatch_action_event("dispatch report"), _msg_event("newer answer")]
        self.assertEqual(extract_latest_answer(events), "newer answer")

    def test_blank_dispatch_next_turn_thought_returns_empty(self):
        events = [_dispatch_action_event("   \n  ")]
        self.assertEqual(extract_latest_answer(events), "")

    # --- issue #830 regression: full report lives in finish.message, the turn
    # text is only a one-line lead-in (#822) ---

    def test_typed_finish_message_captured_when_no_standalone_answer(self):
        events = [
            _finish_event_with_thought(
                "Work pushed and PR updated. Here is my wind-down summary.",
                "# Full report\n\nBody with the complete deliverable.",
            )
        ]
        self.assertEqual(
            extract_latest_answer(events),
            "# Full report\n\nBody with the complete deliverable.",
        )

    def test_typed_finish_message_beats_an_earlier_standalone_answer(self):
        """#822: the terminator's typed message is the deliverable.

        An earlier one-line ack (or any work-loop message) shares the stream
        with the wind-down ``finish`` whose ``message`` holds the full report;
        the report must win rather than the short turn text."""
        events = [
            _msg_event("Acknowledged, starting work."),
            _finish_event_with_thought(
                "Work pushed and PR updated. Here is my wind-down summary.",
                "# Full report\n\nBody with the complete deliverable.",
            ),
        ]
        self.assertEqual(
            extract_latest_answer(events),
            "# Full report\n\nBody with the complete deliverable.",
        )

    def test_finish_message_wins_over_an_earlier_richer_standalone_answer(self):
        """Args win: the finish contract puts the deliverable in ``message``."""
        events = [
            _msg_event("# Earlier draft\n\nsuperseded by the final report"),
            _finish_event_with_thought("", "# Final report\n\nposted body"),
        ]
        self.assertEqual(
            extract_latest_answer(events), "# Final report\n\nposted body"
        )

    def test_empty_finish_message_falls_back_to_the_turn_text(self):
        events = [
            _msg_event("earlier answer"),
            _finish_event_with_thought("finish report", ""),
        ]
        self.assertEqual(extract_latest_answer(events), "finish report")

    def test_later_finish_message_replaces_an_earlier_finish_message(self):
        events = [
            _finish_event_with_thought("note one", "# first report"),
            _finish_event_with_thought("note two", "# second report"),
        ]
        self.assertEqual(extract_latest_answer(events), "# second report")


class TestTerminalTurnAnswer(unittest.TestCase):
    """Per-tool source selection on the control-plane turn (issues #822/#830).

    ``finish`` answers with its typed ``message`` argument; ``dispatch_next``
    answers with its turn text because its arguments are routing metadata.
    """

    def test_finish_typed_message_is_used(self):
        event = _finish_event_with_thought("short lead-in", "# Full report")
        self.assertEqual(_terminal_turn_answer(event, "finish"), "# Full report")

    def test_finish_empty_message_falls_back_to_thought(self):
        event = _finish_event_with_thought("finish report", "")
        self.assertEqual(_terminal_turn_answer(event, "finish"), "finish report")

    def test_finish_without_action_falls_back_to_thought(self):
        event = _finish_action_event("turn-text deliverable")
        self.assertEqual(
            _terminal_turn_answer(event, "finish"), "turn-text deliverable"
        )

    def test_dispatch_next_answers_with_thought_not_args(self):
        event = _dispatch_action_event("Full context report")
        self.assertEqual(
            _terminal_turn_answer(event, "dispatch_next"), "Full context report"
        )

    def test_other_tool_returns_empty(self):
        from openhands.sdk.event import ActionEvent
        from openhands.sdk.llm.message import MessageToolCall, TextContent

        event = ActionEvent(
            source="agent",
            tool_name="terminal",
            thought=[TextContent(text="mid-run commentary")],
            tool_call_id="tc-x",
            tool_call=MessageToolCall(
                id="tc-x", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="r-x",
        )
        self.assertEqual(_terminal_turn_answer(event, "finish"), "")


def _finalize_action_event(text: str, tool_name: str | None = "finalize") -> "object":
    """Build the finalize-turn ActionEvent the SDK emits (issue #1020).

    The action carries no declared field (the report rides in the turn text), so
    the generic SDK action model dumps it as ``{}`` and the turn is recognizable
    only by its tool name — the same shape the engine harvests at runtime.
    """
    from agent_script.features.finalize_tool.finalize_tool import FinalizeAction
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    tool_call = MessageToolCall(
        id="tc-finalize",
        name=tool_name or "finalize",
        arguments="{}",
        origin="completion",
    )
    return ActionEvent(
        source="agent",
        tool_name=tool_name,
        thought=[TextContent(text=text)],
        action=FinalizeAction(),
        tool_call_id="tc-finalize",
        tool_call=tool_call,
        llm_response_id="r-finalize",
    )


class TestEngineOwnedTerminalTurns(unittest.TestCase):
    """Issue #1020: engine-owned turns never supply the posted answer."""

    def test_the_recall_prompt_echo_is_not_captured(self):
        events = [
            _msg_event("the real work-loop answer"),
            _finish_action_event(DISPATCH_RECALL_PROMPT),
        ]
        self.assertEqual(
            extract_latest_answer(events, classify_event=_is_recall_pass_turn),
            "the real work-loop answer",
        )

    def test_an_agent_answer_on_the_recall_turn_still_wins(self):
        events = [
            _msg_event("the real work-loop answer"),
            _finish_action_event("supplied the missing verdict"),
        ]
        self.assertEqual(
            extract_latest_answer(events, classify_event=_is_recall_pass_turn),
            "supplied the missing verdict",
        )

    def test_the_guard_is_pinned_to_the_injected_prompt(self):
        """An agent turn quoted around the prompt is still an answer."""
        events = [_finish_action_event(f"{DISPATCH_RECALL_PROMPT}\nverdict: approved")]
        self.assertEqual(
            extract_latest_answer(events, classify_event=_is_recall_pass_turn),
            f"{DISPATCH_RECALL_PROMPT}\nverdict: approved",
        )

    def test_unclassified_control_turns_keep_latest_wins(self):
        events = [
            _finish_action_event("the real work-loop answer"),
            _finish_action_event("a later real answer"),
        ]
        self.assertEqual(
            extract_latest_answer(events, classify_event=_is_recall_pass_turn),
            "a later real answer",
        )

    def test_the_engine_finalize_turn_is_never_captured(self):
        """Its text is the engine prompt, which embeds the section skeleton."""
        events = [
            _msg_event("the real work-loop answer"),
            _finalize_action_event("Write your report as section bodies…"),
        ]
        self.assertEqual(extract_latest_answer(events), "the real work-loop answer")

    def test_the_closing_turn_never_supplies_the_work_loop_answer(self):
        """Repro #1038: the agent acknowledges the closing finalize call.

        The ack ("Report posted above...") follows the closing turn; it must not
        replace the work-loop answer the fallback chain would otherwise post.
        """
        from types import SimpleNamespace

        from agent_script.features.build_output.build_output import _work_loop_events

        events = [
            _msg_event("the real work-loop answer"),
            _finalize_action_event(""),
            _msg_event("Report posted above and turn outcome recorded."),
        ]
        closing = SimpleNamespace(nebuko_report_event_offset=1)
        self.assertEqual(
            extract_latest_answer(_work_loop_events(events, closing)),
            "the real work-loop answer",
        )

    def test_without_a_closing_turn_the_events_are_unchanged(self):
        from types import SimpleNamespace

        from agent_script.features.build_output.build_output import _work_loop_events

        events = [_msg_event("answer")]
        self.assertEqual(_work_loop_events(events, SimpleNamespace()), events)

    def test_the_legacy_finish_and_dispatch_turns_are_unaffected(self):
        events = [
            _dispatch_action_event("dispatch report"),
            _finish_action_event("finish report"),
        ]
        self.assertEqual(extract_latest_answer(events), "finish report")


class TestExtractStandaloneAnswer(unittest.TestCase):
    """The standalone-text source kept for the SCAFFOLD_BRANCH marker (issue #636)."""

    def test_ignores_control_plane_turns(self):
        events = [
            _msg_event("standalone deliverable"),
            _finish_event_with_thought("lead-in", "# report in finish.message"),
        ]
        self.assertEqual(extract_standalone_answer(events), "standalone deliverable")

    def test_latest_standalone_wins_and_user_messages_ignored(self):
        events = [
            _msg_event("earlier answer"),
            _user_event("user interjection"),
            _msg_event("latest answer"),
        ]
        self.assertEqual(extract_standalone_answer(events), "latest answer")

    def test_empty_when_only_control_turns(self):
        events = [_finish_action_event("finish report")]
        self.assertEqual(extract_standalone_answer(events), "")
        self.assertEqual(extract_standalone_answer([]), "")
        self.assertEqual(extract_standalone_answer(None), "")


def _outcome_with_offset(offset) -> "object":
    """Fake RunOutcome carrying the fields build_output reads."""
    from agent_script.features.run_agent.run_outcome import OutcomeStatus

    return SimpleNamespace(
        status=OutcomeStatus.SUCCESS, dispatch_recall_event_offset=offset
    )


class TestExtractLatestAnswerRecallOffset(unittest.TestCase):
    """Recall-aware capture: pre-recall deliverable survives the recall pass (issue #558).

    Mirrors the exact #551 ordering: the agent posts its full proposal, ends
    without a dispatch decision, the engine injects a recall turn whose short
    routing/status note would otherwise win as the "latest answer" and be
    posted in place of the proposal.
    """

    def test_pre_recall_message_wins_with_offset(self):
        """[proposal, recall user msg, finish status note] with the offset
        recorded after the proposal → the full proposal is captured."""
        events = [
            _msg_event("Full architecture proposal"),
            _user_event("DISPATCH_RECALL_PROMPT injected"),
            _finish_action_event("Holding workflow pending review"),
        ]
        # Offset captured just before the first recall pass (after index 0).
        selected = _recall_aware_events(events, _outcome_with_offset(1))
        self.assertEqual(
            extract_latest_answer(selected), "Full architecture proposal"
        )

    def test_dispatch_next_thought_after_offset_ignored(self):
        """A dispatch_next turn thought emitted during the recall pass must
        not replace the pre-recall deliverable."""
        events = [
            _msg_event("Full architecture proposal"),
            _dispatch_action_event("short routing note"),
        ]
        selected = _recall_aware_events(events, _outcome_with_offset(1))
        self.assertEqual(
            extract_latest_answer(selected), "Full architecture proposal"
        )

    def test_no_offset_keeps_latest_wins(self):
        """offset=None (no recall happened) → byte-identical legacy
        latest-wins behavior, so the #553/#555 suites stay green."""
        events = [
            _msg_event("Full architecture proposal"),
            _dispatch_action_event("short routing note"),
        ]
        self.assertIs(_recall_aware_events(events, _outcome_with_offset(None)), events)
        self.assertEqual(
            extract_latest_answer(_recall_aware_events(events, _outcome_with_offset(None))),
            "short routing note",
        )

    def test_none_run_outcome_returns_full_list(self):
        """Conversations with no attached RunOutcome (pre-#524 compat) keep
        the full event stream untouched."""
        events = [_msg_event("answer")]
        self.assertIs(_recall_aware_events(events, None), events)

    def test_offset_zero_excludes_everything(self):
        """A degenerate offset of 0 yields the empty fallback ("")."""
        events = [_msg_event("proposal"), _finish_action_event("note")]
        selected = _recall_aware_events(events, _outcome_with_offset(0))
        self.assertEqual(extract_latest_answer(selected), "")

    def test_offset_beyond_length_is_safe(self):
        """An offset past the end of the stream degrades to the full list
        (slicing clamps) — latest-wins over everything."""
        events = [_msg_event("earlier answer"), _msg_event("latest answer")]
        selected = _recall_aware_events(events, _outcome_with_offset(99))
        self.assertEqual(extract_latest_answer(selected), "latest answer")

    def test_build_output_writes_pre_recall_deliverable(self):
        """End-to-end at the call site: with a recall offset, the
        latest-answer artifact holds the proposal, NOT the recall-turn
        status note (the #551 failure mode)."""
        from types import SimpleNamespace as NS

        import build_output as bo

        events = [
            _msg_event("Full architecture proposal"),
            _user_event("DISPATCH_RECALL_PROMPT injected"),
            _finish_action_event("Holding workflow pending review"),
        ]
        conversation = NS(
            state=NS(events=events),
            id="conv-test-558",
            run_outcome=_outcome_with_offset(1),
        )
        metrics = NS(review_fix_requests=0, to_dict=lambda: {})

        def _fake_write(files, parts, state):
            _fake_write.final_message = files

        with mock.patch.object(bo, "get_agent_final_response", return_value="finish summary"), \
             mock.patch.object(bo, "_load_prev_state", return_value=None), \
             mock.patch.object(bo, "_build_issue_info", return_value=None), \
             mock.patch.object(bo, "_build_pr_info", return_value=None), \
             mock.patch.object(bo, "_build_resolution", return_value=None), \
             mock.patch.object(
                 bo, "_resolve_outcome",
                 return_value=(
                     TurnOutcome(
                         status=TurnStatus.INCOMPLETE,
                         incomplete_reason="no_outcome",
                     ),
                     RunPrefs(),
                     None,
                 ),
             ), \
             mock.patch.object(bo, "clear_run"), \
             mock.patch.object(
                 bo, "apply_gate_lock",
                 side_effect=lambda outcome, **kw: (outcome, None),
             ), \
             mock.patch.object(bo, "build_state", return_value=NS(run_id=1)), \
             mock.patch.object(bo, "split_message", return_value=[]), \
             mock.patch.object(bo, "_write_output_files", side_effect=_fake_write):
            with tempfile.TemporaryDirectory() as tmp:
                cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    bo.build_output(conversation, metrics)
                    with open(LATEST_ANSWER_FILE, encoding="utf-8") as f:
                        captured = f.read()
                finally:
                    os.chdir(cwd)

        self.assertEqual(captured, "Full architecture proposal")
        # The finish message (status note) still flows through untouched —
        # only the answer capture was recall-sliced.
        self.assertEqual(_fake_write.final_message, "finish summary")


class TestCommentSplitLength(unittest.TestCase):
    """Step-5 seam: build_output splits at the shared CommentSizePolicy (#913)."""

    def test_uses_policy_max_comment_chars(self):
        from build_output import _comment_split_length
        from config import CommentSizePolicy

        self.assertEqual(
            _comment_split_length(),
            CommentSizePolicy().resolved_max_comment_chars(),
        )

    def test_default_is_the_full_comment_cap(self):
        from build_output import _comment_split_length

        # The old role branch gave non-context agents a 10k split; the policy
        # lifts every role to the comment cap so a long report is not chopped.
        self.assertEqual(_comment_split_length(), 60_000)


class TestWriteLatestAnswerFile(unittest.TestCase):
    """Artifact persistence used by the prepare_comment ETL layer."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_writes_exact_content(self):
        _write_latest_answer_file("# Deliverable\n\nbody")
        with open(LATEST_ANSWER_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Deliverable\n\nbody")

    def test_empty_string_writes_empty_file(self):
        _write_latest_answer_file("")
        self.assertTrue(os.path.isfile(LATEST_ANSWER_FILE))
        self.assertEqual(os.path.getsize(LATEST_ANSWER_FILE), 0)


def _finish_event(message: str):
    """Build a real finish ActionEvent so get_agent_final_response returns ``message``.

    Distinct from ``_finish_action_event`` above: that helper only carries
    thought text for the capture layer; the SDK's final-response channel
    (``get_agent_final_response``) reads ``ActionEvent.action`` instead.
    """
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall
    from openhands.sdk.tool.builtins.finish import FinishAction

    tool_call = MessageToolCall(
        id="tc-final-finish",
        name="finish",
        arguments=json.dumps({"message": message}),
        origin="completion",
    )
    return ActionEvent(
        source="agent",
        tool_name="finish",
        thought=[],
        tool_call_id="tc-final-finish",
        tool_call=tool_call,
        llm_response_id="r-final",
        action=FinishAction(message=message),
    )


class TestBuildOutputScaffoldBranchWiring(unittest.TestCase):
    """End-to-end SCAFFOLD_BRANCH wiring in build_output (issue #636):
    dual-source extraction, state persistence, and strip of both texts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        env_patch = mock.patch.dict(
            "os.environ",
            {
                "ROLE_NAME": "dev",
                "WORKFLOW_NAME": "plan-orchestration",
                "WORKFLOW_STEP": "scaffold",
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _run(self, finish_message, latest_answer_text):
        from metrics_schema import Metrics
        from build_output import build_output

        conv = SimpleNamespace(
            state=SimpleNamespace(
                events=[_msg_event(latest_answer_text), _finish_event(finish_message)]
            )
        )
        return build_output(conv, Metrics(agent_role="dev"), None, prev_state=None)

    def test_marker_only_in_standalone_answer_persisted_and_stripped_both(self):
        finish_note = "Scaffolding complete, handing off."
        answer = (
            "# Report\n\nSCAFFOLD_BRANCH: feature/issue-635-workflow-command\n"
            "Done.\n"
        )
        result = self._run(finish_note, answer)

        # Persisted in the built state (and therefore the posted state JSON).
        self.assertEqual(
            result["workflow_state"].scaffold_branch,
            "feature/issue-635-workflow-command",
        )
        state_json = state_to_json(result["workflow_state"])
        self.assertIn('"scaffold_branch"', state_json)
        self.assertIn("feature/issue-635-workflow-command", state_json)

        # Posted body: the deliverable is the finish message (the standalone
        # turn only carries the marker); the summary line is still appended.
        self.assertNotIn("SCAFFOLD_BRANCH:", result["final_message"])
        self.assertIn(
            "Scaffold branch: `feature/issue-635-workflow-command`",
            result["final_message"],
        )

        # Latest-answer file holds the deliverable (finish message), marker-free.
        with open(LATEST_ANSWER_FILE, encoding="utf-8") as f:
            written = f.read()
        self.assertNotIn("SCAFFOLD_BRANCH:", written)
        self.assertEqual(written, finish_note)

    def test_finish_message_marker_still_wins_and_is_stripped(self):
        result = self._run(
            "Scaffolding done.\nSCAFFOLD_BRANCH: feature/issue-1-finish",
            "SCAFFOLD_BRANCH: feature/issue-1-answer\nBody.",
        )
        self.assertEqual(result["workflow_state"].scaffold_branch, "feature/issue-1-finish")
        self.assertNotIn("SCAFFOLD_BRANCH:", result["final_message"])
        self.assertIn("Scaffold branch: `feature/issue-1-finish`", result["final_message"])
        with open(LATEST_ANSWER_FILE, encoding="utf-8") as f:
            self.assertNotIn("SCAFFOLD_BRANCH:", f.read())

    def test_malformed_finish_valid_answer_falls_back(self):
        result = self._run(
            "SCAFFOLD_BRANCH: 'broken'",
            "SCAFFOLD_BRANCH: feature/issue-1-fallback\nBody.",
        )
        self.assertEqual(
            result["workflow_state"].scaffold_branch, "feature/issue-1-fallback"
        )
        with open(LATEST_ANSWER_FILE, encoding="utf-8") as f:
            self.assertNotIn("SCAFFOLD_BRANCH:", f.read())

    def test_malformed_in_both_not_persisted(self):
        result = self._run(
            "SCAFFOLD_BRANCH: 'broken'", "SCAFFOLD_BRANCH: -also-broken\nBody."
        )
        self.assertIsNone(result["workflow_state"].scaffold_branch)
        # No successful persistence → texts posted as-is, no summary line.
        self.assertNotIn("Scaffold branch:", result["final_message"])
        with open(LATEST_ANSWER_FILE, encoding="utf-8") as f:
            self.assertIn("SCAFFOLD_BRANCH:", f.read())

    def test_no_marker_leaves_latest_answer_file_untouched(self):
        result = self._run("Plain finish note.", "# Report\n\nBody only.")
        self.assertIsNone(result["workflow_state"].scaffold_branch)
        # The file holds the deliverable — the finish message — unchanged, and
        # no summary line is appended since no marker was seen.
        with open(LATEST_ANSWER_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read(), "Plain finish note.")


class TestBuildOutputScaffoldBranchToolWiring(unittest.TestCase):
    """End-to-end report_scaffold_branch tool wiring (issue #636 rework):
    tool-first precedence, marker fallback intact, registry cleanup."""

    def setUp(self):
        from agent_script.features.scaffold_branch_tool import reset_registry

        self._reset_registry = reset_registry
        reset_registry()
        self.addCleanup(reset_registry)
        self._conv_id = "tool-run-1"
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        env_patch = mock.patch.dict(
            "os.environ",
            {
                "ROLE_NAME": "dev",
                "WORKFLOW_NAME": "plan-orchestration",
                "WORKFLOW_STEP": "scaffold",
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _run(self, finish_message, latest_answer_text):
        from metrics_schema import Metrics
        from build_output import build_output

        conv = SimpleNamespace(
            id=self._conv_id,
            state=SimpleNamespace(
                events=[_msg_event(latest_answer_text), _finish_event(finish_message)]
            ),
        )
        return build_output(conv, Metrics(agent_role="dev"), None, prev_state=None)

    def _record_tool(self, branch):
        from agent_script.features.scaffold_branch_tool import record_scaffold_branch

        record_scaffold_branch(self._conv_id, branch)

    def test_tool_recorded_no_markers_persisted_and_registry_cleared(self):
        self._record_tool("feature/issue-635-tool")
        result = self._run(
            "Scaffolding complete, handing off.", "# Report\n\nBody only."
        )

        # Persisted from the tool channel; no marker present, so no strip.
        self.assertEqual(
            result["workflow_state"].scaffold_branch, "feature/issue-635-tool"
        )
        state_json = state_to_json(result["workflow_state"])
        self.assertIn("feature/issue-635-tool", state_json)
        self.assertNotIn("Scaffold branch:", result["final_message"])

        # Run-scoped registry cleaned up on the exit path.
        from agent_script.features.scaffold_branch_tool import (
            get_recorded_scaffold_branch,
        )

        self.assertIsNone(get_recorded_scaffold_branch(self._conv_id))

    def test_tool_beats_conflicting_markers_markers_still_stripped(self):
        self._record_tool("feature/issue-635-tool")
        result = self._run(
            "Scaffolding done.\nSCAFFOLD_BRANCH: feature/issue-1-finish-marker",
            "SCAFFOLD_BRANCH: feature/issue-1-answer-marker\nBody.",
        )

        # Tool value wins over both prose markers.
        self.assertEqual(result["workflow_state"].scaffold_branch, "feature/issue-635-tool")
        # The raw prose markers are still stripped (they are prose, whatever
        # won the precedence).
        self.assertNotIn("SCAFFOLD_BRANCH:", result["final_message"])
        with open(LATEST_ANSWER_FILE, encoding="utf-8") as f:
            self.assertNotIn("SCAFFOLD_BRANCH:", f.read())

    def test_no_tool_marker_fallback_unchanged(self):
        result = self._run(
            "Scaffolding done.",
            "Report.\nSCAFFOLD_BRANCH: feature/issue-635-marker\nDone.",
        )
        self.assertEqual(
            result["workflow_state"].scaffold_branch, "feature/issue-635-marker"
        )
        self.assertNotIn("SCAFFOLD_BRANCH:", result["final_message"])


class TestLoadPrevStateHandoff(unittest.TestCase):
    """Anomaly B: the prev-state hand-off must never silently drop history."""

    def setUp(self):
        self._env = dict(os.environ)
        os.environ["REPO_NAME"] = "owner/repo"
        os.environ["TARGET_NUMBER"] = "648"
        self._tmp = tempfile.mkdtemp()
        self._prev_runner_temp = os.environ.get("RUNNER_TEMP")
        os.environ["RUNNER_TEMP"] = self._tmp

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        if self._prev_runner_temp is None:
            os.environ.pop("RUNNER_TEMP", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    @staticmethod
    def _state(run_id=7):
        return WorkflowState(
            run_id=run_id,
            workflow="plan-orchestration",
            step="scaffold",
            agent="dev",
        )

    def _comment(self, state):
        return {"id": 1, "body": format_collapsed_section(state) + "answer"}

    def test_absent_handoff_falls_back_to_comments(self):
        calls = {}

        def fake_fetch(repo, number):
            calls["args"] = (repo, number)
            return [self._comment(self._state())]

        state = load_prev_state_or_recover(fetch_comments=fake_fetch)
        self.assertIsNotNone(state)
        self.assertEqual(state.run_id, 7)
        self.assertEqual(calls["args"], ("owner/repo", 648))

    def test_valid_handoff_does_not_fetch_comments(self):
        from workflow_state.state_codec import WorkflowStateCodec

        WorkflowStateCodec().save_prev(self._state())

        def fail_fetch(*_args, **_kwargs):
            raise AssertionError("comments must not be fetched on a valid hand-off")

        state = load_prev_state_or_recover(fetch_comments=fail_fetch)
        self.assertIsNotNone(state)
        self.assertEqual(state.run_id, 7)

    def test_invalid_handoff_falls_back_to_comments(self):
        with open(os.path.join(self._tmp, "prev_workflow_state.json"), "w") as f:
            f.write("{ not valid json")

        state = load_prev_state_or_recover(
            fetch_comments=lambda _r, _n: [self._comment(self._state(run_id=9))]
        )
        self.assertIsNotNone(state)
        self.assertEqual(state.run_id, 9)

    def test_default_recovery_reads_a_bounded_tail(self):
        """The comment recovery path must not re-paginate the whole thread (#805)."""
        from unittest.mock import patch

        from thread_snapshot import state_tail_limit

        with patch(
            "build_output.get_issue_comments",
            return_value=[self._comment(self._state(run_id=11))],
        ) as fetch:
            state = load_prev_state_or_recover()

        self.assertIsNotNone(state)
        self.assertEqual(state.run_id, 11)
        fetch.assert_called_once_with("owner/repo", 648, limit=state_tail_limit())

    def test_missing_repo_env_returns_none(self):
        os.environ.pop("REPO_NAME", None)
        self.assertIsNone(load_prev_state_or_recover(fetch_comments=lambda *_a: []))


class TestBuildOutputPrevStateIntegration(unittest.TestCase):
    """The recovery seam must live *inside* ``build_output`` (issue #671).

    Round-2 review showed the helper was tested in isolation while the
    production ``build_output`` call site still used the disk-only loader, so
    a missing ``$RUNNER_TEMP`` hand-off could still reset version/history
    (anomaly B of #648). These drive the real builder end-to-end.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        env_patch = mock.patch.dict(
            "os.environ",
            {
                "ROLE_NAME": "dev",
                "WORKFLOW_NAME": "plan-orchestration",
                "WORKFLOW_STEP": "sub_impl",
                "REPO_NAME": "owner/repo",
                "TARGET_NUMBER": "648",
                "RUNNER_TEMP": self._tmp.name,
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.addCleanup(os.chdir, self._cwd)
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def _recovered_v7() -> WorkflowState:
        return WorkflowState(
            run_id=7,
            workflow="plan-orchestration",
            step="scaffold",
            agent="dev",
            history=[
                HistoryEntry(
                    step=f"step_{i}",
                    agent="architect",
                    summary=f"s{i}",
                    turn_outcome=TurnOutcome(
                        status=TurnStatus.COMPLETED, appreciation="approved"
                    ),
                )
                for i in range(5)
            ],
        )

    @staticmethod
    def _conversation() -> SimpleNamespace:
        return SimpleNamespace(
            state=SimpleNamespace(
                events=[
                    _msg_event("# Report\n\nFilled the stub bodies."),
                    _finish_event("Implement step complete."),
                ]
            )
        )

    def _run(self, **kwargs):
        from metrics_schema import Metrics
        from build_output import build_output

        return build_output(
            self._conversation(), Metrics(agent_role="dev"), None, **kwargs
        )

    def test_provided_prev_state_is_carried_into_output_state(self):
        result = self._run(prev_state=self._recovered_v7())

        state = result["workflow_state"]
        self.assertEqual(state.run_id, 8)
        self.assertEqual(
            [h.step for h in state.history],
            [f"step_{i}" for i in range(5)] + ["sub_impl"],
        )

    def test_comments_fallback_reaches_output_state_end_to_end(self):
        """Production path: ``agent_script`` recovers (disk miss → comments)."""
        from build_output import load_prev_state_or_recover

        comment = {
            "id": 1,
            "body": format_collapsed_section(self._recovered_v7()) + "answer",
        }
        recovered = load_prev_state_or_recover(fetch_comments=lambda *_a: [comment])
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.run_id, 7)

        result = self._run(prev_state=recovered)

        state = result["workflow_state"]
        self.assertEqual(state.run_id, 8)
        self.assertEqual(
            [h.step for h in state.history],
            [f"step_{i}" for i in range(5)] + ["sub_impl"],
        )

    def test_provided_state_is_authoritative_and_never_re_read(self):
        import build_output as bo

        with mock.patch.object(
            bo, "_load_prev_state", side_effect=AssertionError("disk re-read")
        ) as loader:
            result = self._run(prev_state=self._recovered_v7())

        loader.assert_not_called()
        self.assertEqual(result["workflow_state"].run_id, 8)

    def test_absent_handoff_does_not_invent_history(self):
        import build_output as bo

        with mock.patch.object(bo, "_load_prev_state", return_value=None):
            result = self._run(prev_state=None)

        state = result["workflow_state"]
        self.assertEqual(state.run_id, 1)
        self.assertEqual(len(state.history), 1)


if __name__ == "__main__":
    unittest.main()
