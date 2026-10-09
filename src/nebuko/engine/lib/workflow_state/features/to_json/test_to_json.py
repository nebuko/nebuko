import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from workflow_state import WorkflowState, HistoryEntry, StateMetrics
from workflow_state.features.to_json.to_json import _format_json, state_to_json


class TestScalarArraysStayInline(unittest.TestCase):
    """Scalar arrays render on one line so metrics no longer bloat the JSON
    (issue #845)."""

    def test_numeric_array_renders_on_one_line(self):
        data = {"agent_runtimes_per_call_ms": [11905.55, 11216.18, 15939.03]}
        self.assertEqual(
            _format_json(data, indent=2, level=0),
            '{\n  "agent_runtimes_per_call_ms": [11905.55, 11216.18, 15939.03]\n}',
        )

    def test_empty_array_renders_inline(self):
        self.assertEqual(_format_json({"a": []}, indent=2, level=0), '{\n  "a": []\n}')

    def test_array_of_objects_keeps_multiline_shape(self):
        data = {"history": [{"step": "a"}]}
        self.assertEqual(_format_json(data, indent=2, level=0), json.dumps(data, indent=2))

    def test_mixed_array_keeps_multiline_shape(self):
        data = {"items": [1, {"a": 2}]}
        self.assertEqual(_format_json(data, indent=2, level=0), json.dumps(data, indent=2))

    def test_object_only_structure_matches_json_dumps(self):
        data = {"history": [{"step": "a", "metrics": {"x": 1}}], "empty": {}}
        self.assertEqual(_format_json(data, indent=2, level=0), json.dumps(data, indent=2))


class TestStateToJsonScalarArrays(unittest.TestCase):
    def test_metrics_runtimes_inline_and_round_trip(self):
        metrics = StateMetrics(
            agent_runtimes_per_call_ms=[1000.0, 1500.0, 2500.0],
            tool_calls_per_tool={"terminal": 3},
        )
        state = WorkflowState(
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            history=[HistoryEntry(step="implement", agent="dev", metrics=metrics)],
        )
        serialized = state_to_json(state)
        self.assertIn(
            '"agent_runtimes_per_call_ms": [1000.0, 1500.0, 2500.0]',
            serialized,
        )
        back = WorkflowState.model_validate(json.loads(serialized))
        self.assertEqual(back.history[-1].metrics, metrics)

    def test_compact_output_stays_single_line(self):
        state = WorkflowState(workflow="w", step="s", agent="a")
        compact = state_to_json(state, compact=True)
        self.assertNotIn("\n", compact)
