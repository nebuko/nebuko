import os
import sys
import unittest

from pydantic import ValidationError

from nebuko.engine.lib.workflow_state import (
    Appreciation,
    HoldKind,
    IncompleteReason,
    RunPrefs,
    TurnOutcome,
    TurnStatus,
    WorkflowState,
    build_state,
    go_next,
)
from nebuko.engine.lib.workflow_state.workflow_state import STATE_SCHEMA_VERSION

class TestBuildState(unittest.TestCase):
    """Test build_state() pure state-transition function."""

    def test_initial_state(self):
        state = build_state(None, workflow="default", step="analyze", agent="architect")
        # issue #821: the invocation counter is truthful — the first built
        # state is run 1, independent of the payload shape generation.
        self.assertEqual(state.run_id, 1)
        self.assertEqual(state.schema_version, STATE_SCHEMA_VERSION)
        self.assertEqual(state.workflow, "default")
        self.assertEqual(state.step, "analyze")
        self.assertEqual(state.agent, "architect")
        self.assertEqual(len(state.history), 1)

    def test_append_to_existing(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            summary="Analyzed request",
        )
        next_state = build_state(
            prev,
            workflow="default",
            step="context",
            agent="context",
            summary="Collected files",
        )
        self.assertEqual(next_state.run_id, 2)
        self.assertEqual(len(next_state.history), 2)
        self.assertEqual(next_state.history[0].summary, "Analyzed request")
        self.assertEqual(next_state.history[1].summary, "Collected files")

    def test_phase_derivation(self):
        state = build_state(
            None,
            workflow="default",
            step="implement",
            agent="dev",
            phase="execution",
        )
        self.assertEqual(state.phase, "execution")

    def test_pinning(self):
        state = build_state(
            None,
            workflow="default",
            step="plan",
            agent="architect",
            summary="Architecture proposal",
            pin=True,
        )
        self.assertTrue(state.history[0].pin)

    def test_pin_adds_comment_ids_to_pinned_comment_ids(self):
        state = build_state(
            None,
            workflow="default",
            step="plan",
            agent="architect",
            summary="Architecture proposal",
            pin=True,
            comment_ids=[101, 102],
        )
        self.assertTrue(state.history[0].pin)
        self.assertEqual(state.pinned_comment_ids, [101, 102])

    def test_unpinned_does_not_add_to_pinned_comment_ids(self):
        state = build_state(
            None,
            workflow="default",
            step="dev",
            agent="dev",
            summary="Implementation",
            pin=False,
            comment_ids=[201],
        )
        self.assertEqual(state.pinned_comment_ids, [])

    def test_pinned_comment_ids_persist_and_deduplicate(self):
        prev = build_state(
            None,
            workflow="default",
            step="plan",
            agent="architect",
            summary="Plan",
            pin=True,
            comment_ids=[301],
        )
        nxt = build_state(
            prev,
            workflow="default",
            step="dev",
            agent="dev",
            summary="Impl",
            pin=True,
            comment_ids=[301, 302],
        )
        self.assertEqual(nxt.pinned_comment_ids, [301, 302])

    def test_result_field(self):
        state = build_state(
            None,
            workflow="default",
            step="dev",
            agent="dev",
            summary="Implementation done",
            result="All tests passed",
        )
        self.assertEqual(state.history[0].result, "All tests passed")

    def test_result_defaults_to_none(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
        )
        self.assertIsNone(state.history[0].result)

    def test_metrics_included(self):
        metrics_dict = {"input_tokens": 5000, "output_tokens": 2000}
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            metrics_dict=metrics_dict,
        )
        # Metrics live on the history entry, not at the root (issue #792).
        self.assertIsNotNone(state.history[-1].metrics)
        self.assertEqual(state.history[-1].metrics.input_tokens, 5000)

    def test_resolution_included(self):
        from nebuko.engine.lib.workflow_state import StepResolution
        resolution = StepResolution(
            role_name="dev",
            selected_workflow="spec-driven-design",
            selected_step="implement",
            matched_rule="workflow:spec-driven-design/step:implement",
            timestamp="2024-01-01T00:00:00+00:00",
        )
        state = build_state(
            None,
            workflow="spec-driven-design",
            step="implement",
            agent="dev",
            resolution=resolution,
        )
        self.assertIsNotNone(state.resolution)
        self.assertEqual(state.resolution.role_name, "dev")
        self.assertEqual(state.resolution.matched_rule, "workflow:spec-driven-design/step:implement")

    def test_resolution_defaults_to_none(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
        )
        self.assertIsNone(state.resolution)

    def test_inject_context_default_true(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
        )
        self.assertTrue(state.history[0].inject_context)

    def test_inject_context_explicit_false(self):
        state = build_state(
            None,
            workflow="default",
            step="context",
            agent="context",
            inject_context=False,
        )
        self.assertFalse(state.history[0].inject_context)

    def test_inject_context_explicit_true(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            inject_context=True,
        )
        self.assertTrue(state.history[0].inject_context)

    def test_inject_context_persists_across_entries(self):
        prev = build_state(
            None,
            workflow="default",
            step="context",
            agent="context",
            inject_context=False,
        )
        next_state = build_state(
            prev,
            workflow="default",
            step="implement",
            agent="dev",
            inject_context=True,
        )
        self.assertFalse(next_state.history[0].inject_context)
        self.assertTrue(next_state.history[1].inject_context)


class TestBuildStateScaffoldBranch(unittest.TestCase):
    """scaffold_branch persistence rules (issue #604, stub)."""

    def test_initial_state_has_no_scaffold_branch(self):
        state = build_state(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="architect",
        )
        self.assertIsNone(state.scaffold_branch)

    def test_build_state_sets_scaffold_branch(self):
        state = build_state(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="architect",
            summary="Scaffolded plan",
            scaffold_branch="feature/issue-604-scaffold-branch-fetch",
        )
        self.assertEqual(
            state.scaffold_branch, "feature/issue-604-scaffold-branch-fetch"
        )

    def test_scaffold_branch_persists_when_not_repeated(self):
        prev = build_state(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="architect",
            scaffold_branch="feature/issue-604-scaffold-branch-fetch",
        )
        next_state = build_state(
            prev,
            workflow="plan-orchestration",
            step="sub_impl",
            agent="dev",
        )
        self.assertEqual(
            next_state.scaffold_branch, "feature/issue-604-scaffold-branch-fetch"
        )

    def test_scaffold_branch_last_write_wins_on_rescaffold(self):
        prev = build_state(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="architect",
            scaffold_branch="feature/issue-604-old-scaffold",
        )
        next_state = build_state(
            prev,
            workflow="plan-orchestration",
            step="plan",
            agent="architect",
            scaffold_branch="feature/issue-604-new-scaffold",
        )
        self.assertEqual(
            next_state.scaffold_branch, "feature/issue-604-new-scaffold"
        )

    def test_go_next_threads_scaffold_branch(self):
        state = go_next(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="architect",
            scaffold_branch="feature/issue-604-scaffold-branch-fetch",
        )
        self.assertEqual(
            state.scaffold_branch, "feature/issue-604-scaffold-branch-fetch"
        )

    def test_backward_compat_old_state_json_without_field(self):
        """A state block written before #604 (no scaffold_branch key) loads cleanly."""
        data = {
            "version": 3,
            "workflow": "plan-orchestration",
            "step": "plan",
            "agent": "architect",
            "history": [
                {
                    "step": "analyze",
                    "agent": "architect",
                    "summary": "ok",
                    # ``turn_outcome`` is a required history field (issue #964):
                    # a v3 blob without it is no longer loadable.
                    "turn_outcome": {"status": "incomplete", "incomplete_reason": "no_outcome"},
                }
            ],
        }
        state = WorkflowState.model_validate(data)
        self.assertIsNone(state.scaffold_branch)


class TestHistoryEntryTurnOutcome(unittest.TestCase):
    """History entries record the factual turn outcome of their step (issue #964).

    ``turn_outcome`` replaced the legacy navigation ``dispatch``: the entry
    keeps what the agent verified happened, plus the non-routing run
    preferences, and the top-level field mirrors the most recent run.
    """

    def test_step_outcome_recorded_on_entry(self):
        outcome = TurnOutcome(status=TurnStatus.COMPLETED, appreciation="approved")
        prefs = RunPrefs(complexity="high")
        state = build_state(
            None,
            workflow="w",
            step="plan",
            agent="architect",
            turn_outcome=outcome,
            prefs=prefs,
        )
        self.assertIs(state.history[-1].turn_outcome, outcome)
        self.assertEqual(state.history[-1].prefs.complexity, "high")
        self.assertIs(state.turn_outcome, outcome)
        self.assertEqual(state.prefs.complexity, "high")

    def test_entry_outcome_defaults_to_a_no_outcome_marker(self):
        """A step that recorded nothing still carries a factual outcome.

        The engine-measured default is INCOMPLETE/NO_OUTCOME: "the agent
        produced no verdict", which routes down the linear edge (issue #964).
        """
        state = build_state(None, workflow="w", step="plan", agent="architect")
        outcome = state.history[-1].turn_outcome
        self.assertEqual(outcome.status, TurnStatus.INCOMPLETE)
        self.assertEqual(outcome.incomplete_reason, IncompleteReason.NO_OUTCOME)
        # The top-level field mirrors the *called* outcome, so a caller that
        # passed nothing leaves it unset while the entry still records the fact.
        self.assertIsNone(state.turn_outcome)

    def test_each_entry_carries_its_own_outcome(self):
        first = build_state(
            None,
            workflow="w",
            step="analyze",
            agent="architect",
            turn_outcome=TurnOutcome(
                status=TurnStatus.COMPLETED, appreciation="approved"
            ),
            prefs=RunPrefs(complexity="high"),
        )
        second = build_state(
            first,
            workflow="w",
            step="context",
            agent="context",
            turn_outcome=TurnOutcome(
                status=TurnStatus.COMPLETED,
                appreciation=Appreciation.HOLD,
                hold_kind=HoldKind.AWAITING_INPUT,
                note="awaiting input",
            ),
        )
        self.assertEqual(second.history[0].turn_outcome.status, TurnStatus.COMPLETED)
        self.assertEqual(second.history[0].prefs.complexity, "high")
        self.assertEqual(second.history[1].turn_outcome.appreciation, Appreciation.HOLD)
        # the top-level outcome reflects only the most recent run
        self.assertEqual(second.turn_outcome.appreciation, Appreciation.HOLD)
        self.assertEqual(second.turn_outcome.note, "awaiting input")

    def test_history_outcome_roundtrip(self):
        outcome = TurnOutcome(status=TurnStatus.COMPLETED, appreciation="approved")
        state = build_state(
            None,
            workflow="w",
            step="plan",
            agent="architect",
            turn_outcome=outcome,
            prefs=RunPrefs(complexity="low", pin=True),
        )
        restored = WorkflowState.model_validate_json(state.model_dump_json())
        self.assertIsNotNone(restored.history[-1].turn_outcome)
        self.assertEqual(restored.history[-1].turn_outcome.appreciation, Appreciation.APPROVED)
        self.assertTrue(restored.history[-1].prefs.pin)
        self.assertEqual(restored.history[-1].turn_outcome, restored.turn_outcome)

    def test_history_entry_without_an_outcome_is_rejected(self):
        """``turn_outcome`` is required on every entry (issue #964).

        The migration deliberately drops the legacy dispatch-era blobs: a
        history entry *is* a routing fact, so an entry that cannot state one
        must not load at all.
        """
        data = {
            "version": 3,
            "workflow": "w",
            "step": "plan",
            "agent": "architect",
            "history": [{"step": "analyze", "agent": "architect", "summary": "ok"}],
        }
        with self.assertRaises(ValidationError):
            WorkflowState.model_validate(data)


class TestBuildStateStartedBy(unittest.TestCase):
    """The workflow starter captured once and carried forward (issue #795)."""

    def test_initial_state_captures_the_starter(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            started_by="olivier-norture",
        )
        self.assertEqual(state.started_by, "olivier-norture")

    def test_started_by_defaults_to_none(self):
        state = build_state(None, workflow="default", step="analyze", agent="architect")
        self.assertIsNone(state.started_by)

    def test_carried_forward_verbatim_across_transitions(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            started_by="olivier-norture",
        )
        # A later chained run is triggered by a bot comment; its TRIGGER_AUTHOR
        # must not re-point the human ping at the bot.
        nxt = build_state(
            prev,
            workflow="default",
            step="context",
            agent="context",
            started_by="nebuko-bot",
        )
        self.assertEqual(nxt.started_by, "olivier-norture")

    def test_a_later_run_without_a_starter_keeps_the_original(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            started_by="olivier-norture",
        )
        nxt = build_state(prev, workflow="default", step="context", agent="context")
        self.assertEqual(nxt.started_by, "olivier-norture")

    def test_legacy_state_without_field_loads_none(self):
        data = {
            "version": 3,
            "workflow": "default",
            "step": "analyze",
            "agent": "architect",
            "history": [
                {
                    "step": "analyze",
                    "agent": "architect",
                    "summary": "ok",
                    # ``turn_outcome`` is a required history field (issue #964):
                    # a v3 blob without it is no longer loadable.
                    "turn_outcome": {"status": "incomplete", "incomplete_reason": "no_outcome"},
                }
            ],
        }
        state = WorkflowState.model_validate(data)
        self.assertIsNone(state.started_by)

    def test_started_by_survives_a_state_roundtrip(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            started_by="olivier-norture",
        )
        restored = WorkflowState.model_validate_json(state.model_dump_json())
        self.assertEqual(restored.started_by, "olivier-norture")


class TestBuildStateWorkflowRef(unittest.TestCase):
    """Workflow provenance field (issue #862): the pinned ref of the reusable
    workflow that defined the run, threaded from ``NEBUKO_WORKFLOW_REF``.
    """

    REF = "o/r/.github/workflows/nebuko-workflow.yaml@refs/tags/v1.2"

    def test_recorded_on_first_run(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            workflow_ref=self.REF,
        )
        self.assertEqual(state.workflow_ref, self.REF)

    def test_defaults_to_none(self):
        state = build_state(None, workflow="default", step="analyze", agent="architect")
        self.assertIsNone(state.workflow_ref)

    def test_carried_forward_when_later_run_has_no_ref(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            workflow_ref=self.REF,
        )
        nxt = build_state(prev, workflow="default", step="context", agent="context")
        self.assertEqual(nxt.workflow_ref, self.REF)

    def test_a_later_ref_overwrites_the_previous_one(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="architect",
            workflow_ref=self.REF,
        )
        newer = "o/r/.github/workflows/nebuko-workflow.yaml@refs/heads/main"
        nxt = build_state(
            prev,
            workflow="default",
            step="context",
            agent="context",
            workflow_ref=newer,
        )
        self.assertEqual(nxt.workflow_ref, newer)

    def test_legacy_state_without_field_loads_none(self):
        data = {
            "version": 3,
            "workflow": "default",
            "step": "analyze",
            "agent": "architect",
            "history": [
                {
                    "step": "analyze",
                    "agent": "architect",
                    "summary": "ok",
                    # ``turn_outcome`` is a required history field (issue #964):
                    # a v3 blob without it is no longer loadable.
                    "turn_outcome": {"status": "incomplete", "incomplete_reason": "no_outcome"},
                }
            ],
        }
        state = WorkflowState.model_validate(data)
        self.assertIsNone(state.workflow_ref)
