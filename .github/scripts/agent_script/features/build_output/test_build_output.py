"""Tests for build_output module."""

import json
import os
import tempfile
import unittest
from unittest import mock
from build_output import (
    LATEST_ANSWER_FILE,
    _write_latest_answer_file,
    agent_run_config_to_dispatch,
    apply_human_gate_intercept,
    extract_latest_answer,
    is_malformed_final_message,
)
from validate_output import strip_agent_run_config
from workflow_state import Dispatch


class TestAgentRunConfigToDispatch(unittest.TestCase):
    """Test the agent_run_config_to_dispatch() function."""

    def test_valid_minimal_json(self):
        """Minimal JSON with just task produces correct defaults."""
        data = '{"task": "ctx"}'
        result = agent_run_config_to_dispatch(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.task_hint, "ctx")
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.complexity, "medium")
        self.assertEqual(result.reasoning, "none")
        self.assertIsNone(result.model)

    def test_valid_full_json(self):
        """Full JSON with all fields produces correct Dispatch object."""
        data = json.dumps({
            "status": "loop",
            "task": "dev",
            "complexity": "high",
            "reasoning": "ultra",
            "model": "claude-opus-4-0",
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 50,
            "min_p": 0.1,
            "presence_penalty": 0.5,
            "repetition_penalty": 1.2,
            "workflow": "default",
            "step": "implement",
            "branch": "approved",
            "parent_workflow": "parent",
            "parent_workflow_step": "initiate",
            "skills": "skill1",
        })
        result = agent_run_config_to_dispatch(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "loop")
        self.assertEqual(result.task_hint, "dev")
        self.assertEqual(result.complexity, "high")
        self.assertEqual(result.reasoning, "ultra")
        self.assertEqual(result.model, "claude-opus-4-0")
        self.assertEqual(result.temperature, 0.7)
        self.assertEqual(result.top_p, 0.9)
        self.assertEqual(result.top_k, 50)
        self.assertEqual(result.min_p, 0.1)
        self.assertEqual(result.presence_penalty, 0.5)
        self.assertEqual(result.repetition_penalty, 1.2)
        self.assertEqual(result.workflow, "default")
        self.assertEqual(result.step, "implement")
        self.assertEqual(result.branch, "approved")
        self.assertEqual(result.parent_workflow, "parent")
        self.assertEqual(result.parent_workflow_step, "initiate")
        self.assertEqual(result.skills, "skill1")

    def test_invalid_json_returns_none(self):
        """Invalid JSON returns None."""
        result = agent_run_config_to_dispatch("not valid json")
        self.assertIsNone(result)

    def test_non_dict_json_returns_none(self):
        """JSON that is not a dict (e.g., array) returns None."""
        result = agent_run_config_to_dispatch('[1, 2, 3]')
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        result = agent_run_config_to_dispatch("")
        self.assertIsNone(result)

    def test_none_input_returns_none(self):
        """None input returns None."""
        result = agent_run_config_to_dispatch(None)
        self.assertIsNone(result)

    def test_missing_fields_use_defaults(self):
        """Missing optional fields use correct defaults."""
        data = '{}'
        result = agent_run_config_to_dispatch(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.complexity, "medium")
        self.assertEqual(result.reasoning, "none")
        self.assertIsNone(result.task_hint)

    def test_status_field_override(self):
        """Status field can be overridden from default."""
        data = '{"task": "ctx", "status": "terminate"}'
        result = agent_run_config_to_dispatch(data)
        self.assertEqual(result.status, "terminate")

    def test_nested_dispatch_structure(self):
        """Agents emit {\"dispatch\": {...}, \"complexity\": \"...\"} — must unwrap."""
        data = '{"dispatch": {"status": "advance"}, "complexity": "high"}'
        result = agent_run_config_to_dispatch(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.complexity, "high")

    def test_nested_dispatch_with_all_fields(self):
        """Nested dispatch with multiple fields plus top-level complexity."""
        data = '{"dispatch": {"status": "loop", "task": "dev", "workflow": "custom", "step": "review"}, "complexity": "low"}'
        result = agent_run_config_to_dispatch(data)
        self.assertEqual(result.status, "loop")
        self.assertEqual(result.task_hint, "dev")
        self.assertEqual(result.complexity, "low")
        self.assertEqual(result.workflow, "custom")
        self.assertEqual(result.step, "review")

    def test_nested_dispatch_complexity_inside_overrides_top_level(self):
        """If complexity appears both inside dispatch and at top-level, nested wins after unwrap."""
        data = '{"dispatch": {"status": "advance", "complexity": "high"}, "complexity": "low"}'
        result = agent_run_config_to_dispatch(data)
        # data.update(dispatch) puts "high" into data; data.get("complexity") sees "high"
        self.assertEqual(result.complexity, "high")

    def test_flat_format_still_works(self):
        """Backward compat: flat format without nested dispatch still works."""
        data = '{"status": "advance", "complexity": "medium"}'
        result = agent_run_config_to_dispatch(data)
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.complexity, "medium")


class TestStripAgentRunConfigs(unittest.TestCase):
    """Test the shared strip_agent_run_config() helper used by build_output."""

    def test_removes_single_sentinel(self):
        msg = 'Hello world\n<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        result = strip_agent_run_config(msg)
        self.assertEqual(result, "Hello world")
        self.assertNotIn("AGENT_RUN_CONFIG", result)

    def test_removes_multiple_sentinels(self):
        msg = ("First part\n"
                '<!-- AGENT_RUN_CONFIG {"task": "genius"} -->\n'
                "Middle part\n"
                '<!-- AGENT_RUN_CONFIG {"task": "dev"} -->\n'
                "Last part")
        result = strip_agent_run_config(msg)
        self.assertNotIn("AGENT_RUN_CONFIG", result)
        self.assertIn("First part", result)
        self.assertIn("Middle part", result)
        self.assertIn("Last part", result)
        self.assertNotIn("\n\n\n", result)

    def test_noop_when_no_sentinels(self):
        msg = "Just regular text without any sentinels"
        result = strip_agent_run_config(msg)
        self.assertEqual(result, msg)

    def test_legacy_xml_not_stripped_here(self):
        """Legacy <agentRunConfig> handling lives in prepare_comment only."""
        msg = ('Response text\n'
                '<agentRunConfig>\n'
                '{\n'
                '  "dispatch": {"status": "advance"}\n'
                '}\n'
                '</agentRunConfig>')
        result = strip_agent_run_config(msg)
        self.assertEqual(result, msg)
        self.assertIn("agentRunConfig", result)

    def test_truncated_sentinel_stripped(self):
        msg = 'Response text\n<!-- AGENT_RUN_CONFIG {"task": "dev", "comple'
        result = strip_agent_run_config(msg)
        self.assertEqual(result, "Response text")
        self.assertNotIn("AGENT_RUN_CONFIG", result)

    def test_preserves_agent_metrics(self):
        msg = ("Content\n"
                "<!-- agent-metrics: {} -->\n"
                '<!-- AGENT_RUN_CONFIG {"status": "advance"} -->')
        result = strip_agent_run_config(msg)
        self.assertIn("agent-metrics", result)
        self.assertNotIn("AGENT_RUN_CONFIG", result)


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


class TestHumanGateIntercept(unittest.TestCase):
    """Test apply_human_gate_intercept() — issue #506 human gate boundary."""

    def _write_workflow(self, tmpdir, name, steps_yaml):
        path = os.path.join(tmpdir, f"{name}.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"name: {name}\n"
                f'description: "test workflow"\n'
                f"trigger:\n  agents: [genius]\n"
                f"steps:\n{steps_yaml}"
            )
        return path

    def test_gate_step_advance_non_resume_becomes_hold(self):
        """advance on a human_gate step (not a resume) is overridden to hold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            dispatch = Dispatch(status="advance")
            with mock.patch.dict(os.environ, env, clear=False):
                result = apply_human_gate_intercept(dispatch, "wf", "implement", False)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "hold")
        self.assertEqual(result.reason, "human_gate:implement")

    def test_gate_step_advance_resume_bypasses(self):
        """A /workflow resume run bypasses the gate and passes advance through."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            dispatch = Dispatch(status="advance")
            with mock.patch.dict(os.environ, env, clear=False):
                result = apply_human_gate_intercept(dispatch, "wf", "implement", True)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")

    def test_non_gate_step_advance_unchanged(self):
        """A step without human_gate advances normally."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: plan\n    agent: genius\n    next: implement\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            dispatch = Dispatch(status="advance")
            with mock.patch.dict(os.environ, env, clear=False):
                result = apply_human_gate_intercept(dispatch, "wf", "plan", False)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")

    def test_loop_not_intercepted(self):
        """loop outcomes are never intercepted by the gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            dispatch = Dispatch(status="loop")
            with mock.patch.dict(os.environ, env, clear=False):
                result = apply_human_gate_intercept(dispatch, "wf", "implement", False)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "loop")

    def test_terminate_bypasses_gate(self):
        """terminate outcomes are never intercepted by the gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            dispatch = Dispatch(status="terminate")
            with mock.patch.dict(os.environ, env, clear=False):
                result = apply_human_gate_intercept(dispatch, "wf", "implement", False)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "terminate")

    def test_missing_dispatch_on_gate_step_becomes_hold(self):
        """No dispatch resolves to advance downstream; gate intercepts it as hold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_workflow(
                tmpdir,
                "wf",
                "  - name: implement\n    agent: dev\n    human_gate: true\n    next: review\n",
            )
            env = {"WORKFLOWS_DIR": tmpdir}
            with mock.patch.dict(os.environ, env, clear=False):
                result = apply_human_gate_intercept(None, "wf", "implement", False)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "hold")
        self.assertEqual(result.reason, "human_gate:implement")

    def test_missing_workflow_name_never_crashes(self):
        """Missing workflow name degrades to no-op (no crash, dispatch unchanged)."""
        dispatch = Dispatch(status="advance")
        result = apply_human_gate_intercept(dispatch, "", "implement", False)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")

    def test_missing_workflow_file_degrades_to_no_op(self):
        """A missing/invalid workflow file must not crash and leaves advance intact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"WORKFLOWS_DIR": tmpdir}
            dispatch = Dispatch(status="advance")
            with mock.patch.dict(os.environ, env, clear=False):
                result = apply_human_gate_intercept(dispatch, "does-not-exist", "implement", False)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")


class TestHumanGateRealWorkflowYaml(unittest.TestCase):
    """Regression against the real workflows/spec-driven-impl.yaml (issue #506).

    Ensures the shipped YAML still parses (no validator conflict on the
    next+conditions combo), stub_approval is gated, implement is not, and
    that the load-bearing 'next: implement' fallback keeps routing alive.
    """

    @staticmethod
    def _real_workflows_dir():
        from pathlib import Path
        return str(Path(__file__).resolve().parents[5] / "workflows")

    def setUp(self):
        self.env = {"WORKFLOWS_DIR": self._real_workflows_dir()}

    def test_real_yaml_gated_step_intercepts_advance(self):
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = apply_human_gate_intercept(
                Dispatch(status="advance"), "spec-driven-impl", "stub_approval", False
            )
        self.assertEqual(result.status, "hold")
        self.assertEqual(result.reason, "human_gate:stub_approval")

    def test_real_yaml_gated_step_resume_bypasses(self):
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = apply_human_gate_intercept(
                Dispatch(status="advance"), "spec-driven-impl", "stub_approval", True
            )
        self.assertEqual(result.status, "advance")

    def test_real_yaml_non_gated_step_unchanged(self):
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = apply_human_gate_intercept(
                Dispatch(status="advance"), "spec-driven-impl", "implement", False
            )
        self.assertEqual(result.status, "advance")

    def test_real_yaml_plain_advance_from_gated_step_routes_on(self):
        """A plain (non-resume) advance run from stub_approval is intercepted to
        hold, but resolving its downstream routing lands on the implement step's
        agent — proving 'next: implement' is load-bearing for the plain-advance path."""
        from resolve_config import resolve_config
        import json
        prompt_base = tempfile.mkdtemp()
        for role in ("dev", "genius", "ctx", "review"):
            os.makedirs(os.path.join(prompt_base, role), exist_ok=True)
            with open(os.path.join(prompt_base, role, "system.md"), "w") as f:
                f.write(f"# {role}")
        state = {
            "version": 1, "workflow": "spec-driven-impl", "step": "stub_approval",
            "agent": "genius", "phase": "stub_approval", "history": [],
            "dispatch": {"status": "advance", "complexity": "medium"},
        }
        body = "<!-- bot-agent-genius -->\n*Genius answer*\n\n```json\n" \
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


if __name__ == "__main__":
    unittest.main()
