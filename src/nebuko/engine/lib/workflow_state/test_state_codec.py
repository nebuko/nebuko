"""Spec tests for the canonical ``WorkflowState`` codec (issue #671).

Reproduces Anomaly B of issue #648: the trigger side resolved ``version=7``
with 5 history entries and logged ``Saved previous workflow state to
.../prev_workflow_state.json (v7)``, yet the runtime rebuilt state from
``prev_state=None`` (``version=3`` with a single history entry). A lossless,
symmetric encode/decode pair plus an explicit absent/invalid distinction is
the fix (issue #671).
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone


from nebuko.engine.lib.config import StepRef  # noqa: E402
from nebuko.engine.lib.workflow_state import HistoryEntry, TurnOutcome, WorkflowState  # noqa: E402
from nebuko.engine.lib.workflow_state.state_codec import PrevStateLoad, WorkflowStateCodec  # noqa: E402
from nebuko.engine.lib.workflow_state.sub_domains.gate_decision import GateDecision  # noqa: E402
from nebuko.engine.lib.workflow_state.workflow_state import STATE_SCHEMA_VERSION  # noqa: E402

HANDOFF_FILE = "prev_workflow_state.json"


def _branch_mounted_leaf_position() -> list[StepRef]:
    """The 4-ref position observed in the failing #648 run (CI log)."""
    return [
        StepRef(workflow="plan-orchestration", step="sub_impl"),
        StepRef(workflow="tmp-impl", step="step_1"),
        StepRef(workflow="dev-review-loop", step="implement"),
        StepRef(workflow="tmp_impl/workflow.yaml", step=""),
    ]


def _state_v7() -> WorkflowState:
    """The trigger-side state the runtime failed to carry forward."""
    return WorkflowState(
        run_id=7,
        schema_version=STATE_SCHEMA_VERSION,
        workflow="plan-orchestration",
        step="scaffold",
        agent="dev",
        phase="execution",
        scaffold_branch="feature/issue-648-scaffold",
        history=[
            HistoryEntry(
                step=f"step_{i}",
                agent="architect",
                summary=f"s{i}",
                turn_outcome=TurnOutcome(status="completed", appreciation="approved"),
            )
            for i in range(5)
        ],
        position=_branch_mounted_leaf_position(),
    )


class TestCodecRoundTrip(unittest.TestCase):
    def test_roundtrip_preserves_run_id_and_history(self):
        codec = WorkflowStateCodec()

        restored = codec.decode(codec.encode(_state_v7(), compact=True))

        self.assertEqual(restored.run_id, 7)
        self.assertEqual(restored.schema_version, STATE_SCHEMA_VERSION)
        self.assertEqual(len(restored.history), 5)
        self.assertEqual(restored.scaffold_branch, "feature/issue-648-scaffold")

    def test_roundtrip_preserves_position_refs(self):
        codec = WorkflowStateCodec()

        restored = codec.decode(codec.encode(_state_v7(), compact=True))

        self.assertEqual(
            [(ref.workflow, ref.step) for ref in restored.position],
            [
                ("plan-orchestration", "sub_impl"),
                ("tmp-impl", "step_1"),
                ("dev-review-loop", "implement"),
                ("tmp_impl/workflow.yaml", ""),
            ],
        )

    def test_encoded_form_is_valid_json(self):
        raw = WorkflowStateCodec().encode(_state_v7(), compact=True)

        self.assertIsInstance(json.loads(raw), dict)


def _state_with_per_entry_outcomes() -> WorkflowState:
    """A state whose history entries each carry their own turn outcome (#685)."""
    return WorkflowState(
        run_id=4,
        workflow="spec-driven-impl",
        step="plan",
        agent="architect",
        phase="planning",
        history=[
            HistoryEntry(
                step="analyze",
                agent="architect",
                summary="a",
                turn_outcome=TurnOutcome(
                    status="completed",
                    appreciation="approved",
                    note="need context",
                ),
            ),
            HistoryEntry(
                step="context",
                agent="context",
                summary="c",
                turn_outcome=TurnOutcome(
                    status="completed",
                    appreciation="approved",
                    note="handed back",
                ),
            ),
        ],
    )


class TestCodecRoundTripPreservesPerEntryOutcomes(unittest.TestCase):
    """The lossless hand-off must carry each history entry's outcome (issue #685).

    The disk hand-off (``save_prev``/``load_prev``) is the exact path where the
    history was silently dropped in #648, so the replayable movement log added in
    #685 must survive it, not just the comment-body path.
    """

    def test_compact_roundtrip_preserves_the_outcome_per_entry(self):
        codec = WorkflowStateCodec()

        restored = codec.decode(codec.encode(_state_with_per_entry_outcomes(), compact=True))

        self.assertEqual(restored.history[0].turn_outcome.status, "completed")
        self.assertEqual(restored.history[0].turn_outcome.note, "need context")
        self.assertEqual(restored.history[1].turn_outcome.appreciation, "approved")
        self.assertEqual(restored.history[1].turn_outcome.note, "handed back")

    def test_disk_handoff_preserves_the_outcome_per_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            codec = WorkflowStateCodec(runner_temp=tmp)

            codec.save_prev(_state_with_per_entry_outcomes())
            result = codec.load_prev()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.state.history[1].turn_outcome.note, "handed back")


def _gate_decision(status: str, step: str = "plan") -> GateDecision:
    """A deterministic engine-owned gate decision (issue #648)."""
    return GateDecision(
        status=status,
        workflow="plan-orchestration",
        target_step=step,
        decided_by="olivier-norture",
        decided_at=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
        feedback="please revise the X section" if status == "rejected" else "",
        comment_id=809,
        position=_branch_mounted_leaf_position(),
    )


def _state_with_gate_decisions() -> WorkflowState:
    """State carrying the latest decision plus the full audit."""
    approved = _gate_decision("approved", "plan")
    rejected = _gate_decision("rejected", "stub_approval")
    return WorkflowState(
        run_id=9,
        workflow="plan-orchestration",
        step="sub_impl",
        agent="dev",
        phase="execution",
        gate_decision=rejected,
        gate_history=[approved, rejected],
        position=_branch_mounted_leaf_position(),
    )


class TestCodecRoundTripPreservesGateDecisions(unittest.TestCase):
    """The lossless hand-off must carry the engine-owned gate state (#648).

    Gate decisions are the only artifact that releases a ``human_gate``
    step, so both the comment-body codec and the disk hand-off
    (``save_prev``/``load_prev``) must round-trip them unchanged.
    """

    def test_compact_roundtrip_preserves_gate_decision_and_history(self):
        codec = WorkflowStateCodec()

        restored = codec.decode(codec.encode(_state_with_gate_decisions(), compact=True))

        self.assertEqual(restored.gate_decision.status, "rejected")
        self.assertEqual(restored.gate_decision.decided_by, "olivier-norture")
        self.assertEqual(restored.gate_decision.position, _branch_mounted_leaf_position())
        self.assertEqual([d.status for d in restored.gate_history], ["approved", "rejected"])

    def test_disk_handoff_preserves_gate_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            codec = WorkflowStateCodec(runner_temp=tmp)

            codec.save_prev(_state_with_gate_decisions())
            result = codec.load_prev()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.state.gate_decision.comment_id, 809)
        self.assertEqual(len(result.state.gate_history), 2)

    def test_state_without_gate_decisions_roundtrips_to_defaults(self):
        codec = WorkflowStateCodec()

        restored = codec.decode(codec.encode(_state_v7(), compact=True))

        self.assertIsNone(restored.gate_decision)
        self.assertEqual(restored.gate_history, [])

    def test_multiline_feedback_roundtrips_verbatim(self):
        codec = WorkflowStateCodec()
        multiline = "1. Flat\n2. Remove this section\n3. cache_hit_token is fine"
        decision = _gate_decision("rejected", "stub_approval").model_copy(
            update={"feedback": multiline}
        )
        state = _state_with_gate_decisions().model_copy(
            update={"gate_decision": decision}
        )

        restored = codec.decode(codec.encode(state, compact=True))

        self.assertEqual(restored.gate_decision.feedback, multiline)


class TestMalformedPositionNeverSilentlyDropped(unittest.TestCase):
    def test_empty_step_mount_ref_is_preserved(self):
        """The empty-step mount ref from #648 survives encode -> decode."""
        codec = WorkflowStateCodec()

        restored = codec.decode(codec.encode(_state_v7(), compact=True))

        self.assertEqual(len(restored.position), 4)
        self.assertEqual(restored.position[-1].workflow, "tmp_impl/workflow.yaml")
        self.assertEqual(restored.position[-1].step, "")


class TestLoadPrev(unittest.TestCase):
    def test_absent_file_reports_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = WorkflowStateCodec(runner_temp=tmp).load_prev()

        self.assertEqual(result.status, "absent")
        self.assertIsNone(result.state)
        self.assertNotEqual(result.detail, "")

    def test_corrupt_file_reports_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, HANDOFF_FILE), "w", encoding="utf-8") as f:
                f.write("{not json")

            result = WorkflowStateCodec(runner_temp=tmp).load_prev()

        self.assertEqual(result.status, "invalid")
        self.assertIsNone(result.state)
        self.assertNotEqual(result.detail, "")

    def test_save_then_load_roundtrips_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            codec = WorkflowStateCodec(runner_temp=tmp)

            path = codec.save_prev(_state_v7())
            self.assertTrue(os.path.isfile(path))

            result = codec.load_prev()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.detail, "")
        self.assertEqual(result.state.run_id, 7)
        self.assertEqual(len(result.state.history), 5)

    def test_v5_blob_without_turn_outcome_reports_invalid(self):
        """A pre-#964 hand-off cannot be decoded: the outcome is mandatory.

        The migration is deliberately not backward compatible (issue #964):
        every history entry is a routing fact, so a v5 blob whose entries
        carry no ``turn_outcome`` must surface as ``invalid`` rather than be
        silently coerced into a fabricated verdict.
        """
        v5_blob = {
            "version": 5,
            "workflow": "default",
            "step": "implement",
            "agent": "dev",
            "history": [
                {"step": "plan", "agent": "architect", "summary": "planned"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, HANDOFF_FILE), "w", encoding="utf-8") as f:
                json.dump(v5_blob, f)

            result = WorkflowStateCodec(runner_temp=tmp).load_prev()

        self.assertEqual(result.status, "invalid")
        self.assertIsNone(result.state)
        self.assertIn("turn_outcome", result.detail)


class TestPrevStateLoadModel(unittest.TestCase):
    def test_detail_defaults_to_empty(self):
        load = PrevStateLoad(state=None, status="absent")

        self.assertEqual(load.detail, "")
