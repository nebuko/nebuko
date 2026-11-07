"""Round-trip tests for the persisted context manifest (issue #1346).

The engine serializes the prompt's ordered, body-free context manifest into
``CONTEXT_MANIFEST`` before invoking the agent; the agent child parses that env
blob and records it on the advancing step's ``HistoryEntry``, so replay can show
what the agent's context was made of — and in which order — offline.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import build_output as bo
from build_output import build_output
from nebuko.engine.lib.metrics_schema import Metrics
from nebuko.engine.lib.workflow_state import WorkflowState
from nebuko.engine.lib.workflow_state.workflow_state import STATE_SCHEMA_VERSION


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
    '[{"order": 0, "kind": "role_prompt", "source": "role:dev",'
    ' "comment_ids": [], "chars": 4, "sha256": "abc"},'
    ' {"order": 1, "kind": "recent_thread", "source": "selection:declared",'
    ' "comment_ids": ["42"], "chars": 20, "sha256": "def"}]'
)


class TestParseContextManifestEnv(unittest.TestCase):
    def test_absent_value_yields_no_rows(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXT_MANIFEST", None)
            self.assertEqual(bo._parse_context_manifest_env(), [])

    def test_empty_value_yields_no_rows(self):
        with mock.patch.dict(os.environ, {"CONTEXT_MANIFEST": "  "}):
            self.assertEqual(bo._parse_context_manifest_env(), [])

    def test_valid_payload_is_parsed_in_order(self):
        with mock.patch.dict(os.environ, {"CONTEXT_MANIFEST": SERIALIZED}):
            rows = bo._parse_context_manifest_env()
        self.assertEqual([row.order for row in rows], [0, 1])
        self.assertEqual([row.kind for row in rows], ["role_prompt", "recent_thread"])
        self.assertEqual(rows[1].comment_ids, ["42"])

    def test_malformed_json_is_ignored(self):
        with mock.patch.dict(os.environ, {"CONTEXT_MANIFEST": "{not json"}):
            self.assertEqual(bo._parse_context_manifest_env(), [])

    def test_malformed_entry_is_skipped(self):
        payload = '[{"order": 0, "kind": "task"}, {"bad": 1}]'
        with mock.patch.dict(os.environ, {"CONTEXT_MANIFEST": payload}):
            rows = bo._parse_context_manifest_env()
        self.assertEqual([row.kind for row in rows], ["task"])


class TestBuildOutputRecordsContext(unittest.TestCase):
    """End-to-end: the parsed rows land on the advancing HistoryEntry."""

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
                "CONTEXT_MANIFEST": SERIALIZED,
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.addCleanup(os.chdir, self._cwd)
        self.addCleanup(self._tmp.cleanup)

    def test_context_is_recorded_on_the_new_history_entry(self):
        conv = SimpleNamespace(
            state=SimpleNamespace(events=[_msg_event("Implementation done")])
        )
        result = build_output(conv, Metrics(agent_role="dev"), None, prev_state=_prev_state())
        entry = result["workflow_state"].history[-1]
        self.assertEqual(entry.step, "implement")
        self.assertEqual([row.kind for row in entry.context], ["role_prompt", "recent_thread"])
        self.assertEqual(entry.context[1].comment_ids, ["42"])

    def test_absent_env_records_no_context(self):
        os.environ.pop("CONTEXT_MANIFEST", None)
        conv = SimpleNamespace(
            state=SimpleNamespace(events=[_msg_event("Implementation done")])
        )
        result = build_output(conv, Metrics(agent_role="dev"), None, prev_state=_prev_state())
        self.assertEqual(result["workflow_state"].history[-1].context, [])
