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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import StepRef  # noqa: E402
from workflow_state import HistoryEntry, WorkflowState  # noqa: E402
from workflow_state.state_codec import PrevStateLoad, WorkflowStateCodec  # noqa: E402

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
        version=7,
        workflow="plan-orchestration",
        step="scaffold",
        agent="dev",
        phase="execution",
        scaffold_branch="feature/issue-648-scaffold",
        history=[
            HistoryEntry(step=f"step_{i}", agent="genius", summary=f"s{i}")
            for i in range(5)
        ],
        position=_branch_mounted_leaf_position(),
    )


class TestCodecRoundTrip(unittest.TestCase):
    def test_roundtrip_preserves_version_and_history(self):
        codec = WorkflowStateCodec()

        restored = codec.decode(codec.encode(_state_v7(), compact=True))

        self.assertEqual(restored.version, 7)
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
        self.assertEqual(result.state.version, 7)
        self.assertEqual(len(result.state.history), 5)


class TestPrevStateLoadModel(unittest.TestCase):
    def test_detail_defaults_to_empty(self):
        load = PrevStateLoad(state=None, status="absent")

        self.assertEqual(load.detail, "")
