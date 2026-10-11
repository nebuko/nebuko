"""Round-trip tests for persisted step-input records (issue #872).

The engine serializes a step's materialized inputs into ``STEP_INPUT_RESULTS``
before invoking the agent; the agent child parses that env blob and records it
on the advancing step's ``HistoryEntry`` so the deterministic I/O contract lives
in workflow state.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import build_output as bo
from build_output import build_output
from metrics_schema import Metrics
from workflow_state import WorkflowState
from workflow_state.workflow_state import STATE_SCHEMA_VERSION


def _msg_event(text: str) -> "object":
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message

    return MessageEvent(
        llm_message=Message(role="assistant", content=text), source="agent"
    )


def _prev_state() -> WorkflowState:
    return WorkflowState(
        run_id=1,
        schema_version=STATE_SCHEMA_VERSION,
        workflow="default",
        step="plan",
        agent="architect",
        phase="planning",
    )


SERIALIZED = (
    '[{"name": "diff", "binding": "exec", "ok": true, "detail": "",'
    ' "value": "patch\\n", "truncated": false}]'
)


class TestParseStepInputRecordsEnv(unittest.TestCase):
    def test_absent_value_yields_no_records(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STEP_INPUT_RESULTS", None)
            self.assertEqual(bo._parse_step_input_records_env(), [])

    def test_empty_value_yields_no_records(self):
        with mock.patch.dict(os.environ, {"STEP_INPUT_RESULTS": "  "}):
            self.assertEqual(bo._parse_step_input_records_env(), [])

    def test_valid_payload_is_parsed(self):
        with mock.patch.dict(os.environ, {"STEP_INPUT_RESULTS": SERIALIZED}):
            records = bo._parse_step_input_records_env()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "diff")
        self.assertEqual(records[0].binding, "exec")
        self.assertEqual(records[0].value, "patch\n")
        self.assertFalse(records[0].truncated)

    def test_malformed_json_is_ignored(self):
        with mock.patch.dict(os.environ, {"STEP_INPUT_RESULTS": "{not json"}):
            self.assertEqual(bo._parse_step_input_records_env(), [])

    def test_non_list_payload_is_ignored(self):
        with mock.patch.dict(os.environ, {"STEP_INPUT_RESULTS": '{"name": "x"}'}):
            self.assertEqual(bo._parse_step_input_records_env(), [])

    def test_malformed_entry_is_skipped(self):
        payload = '[{"name": "ok", "binding": "str", "ok": true}, {"bad": 1}]'
        with mock.patch.dict(os.environ, {"STEP_INPUT_RESULTS": payload}):
            records = bo._parse_step_input_records_env()
        self.assertEqual([record.name for record in records], ["ok"])


class TestBuildOutputRecordsInputs(unittest.TestCase):
    """End-to-end: the parsed records land on the advancing HistoryEntry."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        env_patch = mock.patch.dict(
            "os.environ",
            {
                "ROLE_NAME": "dev",
                "WORKFLOW_NAME": "default",
                "WORKFLOW_STEP": "implement",
                "STEP_INPUT_RESULTS": SERIALIZED,
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.addCleanup(os.chdir, self._cwd)
        self.addCleanup(self._tmp.cleanup)

    def test_inputs_are_recorded_on_the_new_history_entry(self):
        conv = SimpleNamespace(
            state=SimpleNamespace(events=[_msg_event("Implementation done")])
        )
        result = build_output(conv, Metrics(agent_role="dev"), None, prev_state=_prev_state())
        entry = result["workflow_state"].history[-1]
        self.assertEqual(entry.step, "implement")
        self.assertEqual(len(entry.inputs), 1)
        self.assertEqual(entry.inputs[0].name, "diff")
        self.assertEqual(entry.inputs[0].value, "patch\n")

    def test_absent_env_records_no_inputs(self):
        os.environ.pop("STEP_INPUT_RESULTS", None)
        conv = SimpleNamespace(
            state=SimpleNamespace(events=[_msg_event("Implementation done")])
        )
        result = build_output(conv, Metrics(agent_role="dev"), None, prev_state=_prev_state())
        self.assertEqual(result["workflow_state"].history[-1].inputs, [])
