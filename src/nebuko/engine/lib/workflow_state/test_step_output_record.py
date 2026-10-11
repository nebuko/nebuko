"""Tests for the step-output locator (issue #756, ADR-0013).

``StepOutputRecord`` is the persisted pointer from a step name to the comment
that step posted. Only locators live in state — never comment bodies — and the
latest record for a step wins (a loop re-run re-points the pointer).
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflow_state import WorkflowState, build_state
from workflow_state.workflow_state import STATE_MARKER_START, STATE_MARKER_END
from workflow_state.step_output_record import STEP_OUTPUTS_KEY, StepOutputRecord
from workflow_state.features.to_json.to_json import state_to_json
from workflow_state.features.get_last_state.get_last_state import (
    extract_latest_state_from_comments,
)


class TestStepOutputRecordUpsert(unittest.TestCase):
    def test_upsert_creates_a_locator(self):
        context: dict = {}
        StepOutputRecord(step="plan", comment_id="42").upsert_into(context)
        self.assertEqual(
            context[STEP_OUTPUTS_KEY], [{"step": "plan", "comment_id": "42"}]
        )

    def test_latest_write_wins_for_the_same_step(self):
        context: dict = {}
        StepOutputRecord(step="plan", comment_id="1").upsert_into(context)
        StepOutputRecord(step="plan", comment_id="2").upsert_into(context)
        records = StepOutputRecord.load_all(context)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].comment_id, "2")

    def test_distinct_steps_coexist(self):
        context: dict = {}
        StepOutputRecord(step="plan", comment_id="1").upsert_into(context)
        StepOutputRecord(step="implement", comment_id="2").upsert_into(context)
        self.assertEqual(set(StepOutputRecord.index(context)), {"plan", "implement"})

    def test_index_is_keyed_by_step(self):
        context: dict = {}
        StepOutputRecord(step="plan", comment_id="1").upsert_into(context)
        index = StepOutputRecord.index(context)
        self.assertEqual(index["plan"].comment_id, "1")

    def test_malformed_entries_are_skipped(self):
        context = {STEP_OUTPUTS_KEY: [{"step": "plan"}, "not-a-dict", {"nope": 1}]}
        records = StepOutputRecord.load_all(context)
        self.assertEqual([r.step for r in records], ["plan"])

    def test_missing_key_yields_no_records(self):
        self.assertEqual(StepOutputRecord.load_all({}), [])


class TestBuildStateRecordsStepOutput(unittest.TestCase):
    def test_build_state_records_a_null_id_locator(self):
        state = build_state(None, workflow="w", step="plan", agent="architect")
        records = StepOutputRecord.index(state.current_context)
        self.assertIn("plan", records)
        self.assertIsNone(records["plan"].comment_id)

    def test_loop_rerun_re_points_the_locator(self):
        first = build_state(None, workflow="w", step="plan", agent="architect")
        StepOutputRecord(step="plan", comment_id="1").upsert_into(
            first.current_context
        )
        # A re-run of the same step rebuilds the locator (latest run wins).
        second = build_state(first, workflow="w", step="plan", agent="architect")
        records = StepOutputRecord.index(second.current_context)
        self.assertEqual(len(records), 1)
        self.assertIsNone(records["plan"].comment_id)

    def test_round_trips_through_serialized_state(self):
        state = build_state(None, workflow="w", step="plan", agent="architect")
        StepOutputRecord(step="plan", comment_id="7").upsert_into(
            state.current_context
        )
        reloaded = WorkflowState.model_validate(json.loads(state_to_json(state)))
        records = StepOutputRecord.index(reloaded.current_context)
        self.assertEqual(records["plan"].comment_id, "7")


class TestReadPathBackfill(unittest.TestCase):
    def _state_comment(self, step: str, comment_id: int) -> dict:
        state = build_state(None, workflow="w", step=step, agent="a")
        body = f"{STATE_MARKER_START}\n{state_to_json(state, compact=True)}\n{STATE_MARKER_END}"
        return {"id": comment_id, "body": body}

    def test_read_path_backfills_comment_ids_by_alignment(self):
        comments = [self._state_comment("plan", 101)]
        state = extract_latest_state_from_comments(comments)
        records = StepOutputRecord.index(state.current_context)
        self.assertEqual(records["plan"].comment_id, "101")


if __name__ == "__main__":
    unittest.main()
