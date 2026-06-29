"""Tests for recovery trace extraction and markdown formatting."""

import unittest
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from extract_recovery_trace import (
    ToolCallTrace,
    RecoveryTrace,
    _truncate,
    extract_recovery_trace,
    recovery_trace_to_markdown,
)


class TestTruncate(unittest.TestCase):
    """Test the _truncate helper function."""

    def test_short_text_unchanged(self):
        self.assertEqual(_truncate("hello", 100), "hello")

    def test_exact_length_unchanged(self):
        text = "a" * 500
        self.assertEqual(_truncate(text, 500), text)

    def test_long_text_truncated(self):
        result = _truncate("a" * 1000, 500)
        self.assertTrue(result.endswith("..."))
        self.assertLessEqual(len(result), 503)

    def test_truncation_at_paragraph_break(self):
        text = "first paragraph\n\nsecond paragraph" + " x" * 300
        result = _truncate(text, 200)
        self.assertIn("first paragraph", result)
        self.assertTrue(result.endswith("..."))

    def test_strips_whitespace(self):
        result = _truncate("hello   ", 20)
        self.assertEqual(result, "hello")


class TestToolCallTrace(unittest.TestCase):
    """Test ToolCallTrace dataclass immutability."""

    def test_frozen(self):
        trace = ToolCallTrace(tool_name="terminal", input_summary="ls -la")
        with self.assertRaises(Exception):
            trace.tool_name = "file_editor"

    def test_defaults(self):
        trace = ToolCallTrace(tool_name="think")
        self.assertEqual(trace.output_summary, "")
        self.assertFalse(trace.error)


class TestRecoveryTrace(unittest.TestCase):
    """Test RecoveryTrace dataclass."""

    def test_creation(self):
        trace = RecoveryTrace(
            agent_role="dev",
            total_tool_calls=5,
            errors_count=1,
            tool_calls=[ToolCallTrace(tool_name="terminal")],
        )
        self.assertEqual(trace.total_tool_calls, 5)
        self.assertEqual(len(trace.tool_calls), 1)

    def test_empty_tool_calls(self):
        trace = RecoveryTrace(agent_role="ctx", total_tool_calls=0, errors_count=0)
        self.assertEqual(trace.tool_calls, [])


class TestExtractRecoveryTrace(unittest.TestCase):
    """Test extract_recovery_trace function."""

    def test_none_conversation_returns_none(self):
        self.assertIsNone(extract_recovery_trace(None))

    def test_empty_events_returns_none(self):
        conv = MagicMock()
        conv.state.events = []
        self.assertIsNone(extract_recovery_trace(conv))

    def test_no_agent_actions_returns_none(self):
        """If there are events but no agent ActionEvents, return None."""
        conv = MagicMock()
        # No ActionEvent instances in events
        conv.state.events = [MagicMock()]
        self.assertIsNone(extract_recovery_trace(conv))

    def _make_tool_call(self, tc_id: str, name: str):
        from openhands.sdk.llm.message import MessageToolCall
        return MessageToolCall(id=tc_id, name=name, arguments="{}", origin="completion")

    def _make_action(self, action_id: str, tool_name: str, summary: str):
        from openhands.sdk.event.llm_convertible.action import ActionEvent
        return ActionEvent(
            id=action_id,
            source="agent",
            tool_name=tool_name,
            tool_call_id=f"call_{action_id}",
            thought=[],
            tool_call=self._make_tool_call(f"call_{action_id}", tool_name),
            llm_response_id=f"resp_{action_id}",
            summary=summary,
        )

    def _make_observation(self, obs_id: str, action_id: str, content: str, is_error: bool = False):
        from openhands.sdk.event.llm_convertible.observation import ObservationEvent
        # Use model_construct to bypass pydantic discriminator validation
        return ObservationEvent.model_construct(
            id=obs_id,
            source="environment",
            tool_name="terminal",
            tool_call_id=f"call_{action_id}",
            action_id=action_id,
            observation=MagicMock(content=content, is_error=is_error),
        )

    def _make_conv(self, events: list, prompt_tokens: int = 0, completion_tokens: int = 0):
        conv = MagicMock()
        conv.state.events = events
        conv.conversation_stats.get_combined_metrics.return_value.accumulated_token_usage.prompt_tokens = prompt_tokens
        conv.conversation_stats.get_combined_metrics.return_value.accumulated_token_usage.completion_tokens = completion_tokens
        return conv

    def test_basic_extraction_with_events(self):
        """Verify tool calls are extracted from a conversation with ActionEvent/ObservationEvent pairs."""
        action = self._make_action("act_001", "terminal", "run git status")
        observation = self._make_observation("obs_001", "act_001", "clean working tree")
        conv = self._make_conv([action, observation], 500, 100)

        trace = extract_recovery_trace(conv)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.total_tool_calls, 1)
        self.assertEqual(trace.errors_count, 0)
        self.assertEqual(trace.input_tokens, 500)
        self.assertEqual(trace.output_tokens, 100)
        self.assertEqual(len(trace.tool_calls), 1)
        self.assertEqual(trace.tool_calls[0].tool_name, "terminal")
        self.assertIn("git status", trace.tool_calls[0].input_summary)
        self.assertIn("clean working tree", trace.tool_calls[0].output_summary)
        self.assertFalse(trace.tool_calls[0].error)

    def test_error_detection_from_observation(self):
        """Verify error observations are detected and counted."""
        action = self._make_action("act_err", "terminal", "run bad command")
        observation = self._make_observation("obs_err", "act_err", "Error: command failed", is_error=True)
        conv = self._make_conv([action, observation])

        trace = extract_recovery_trace(conv)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.errors_count, 1)
        self.assertTrue(trace.tool_calls[0].error)

    def test_max_traces_limiting(self):
        """Verify max_traces parameter limits the number of extracted traces."""
        events = []
        for i in range(10):
            events.append(self._make_action(f"act_{i}", "terminal", f"command {i}"))
            events.append(self._make_observation(f"obs_{i}", f"act_{i}", f"result {i}"))
        conv = self._make_conv(events)

        trace = extract_recovery_trace(conv, max_traces=5)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.total_tool_calls, 5)

    def test_actions_without_observations(self):
        """Tool calls without matching observations should still be captured."""
        action = self._make_action("act_no_obs", "file_editor", "view file")
        conv = self._make_conv([action])

        trace = extract_recovery_trace(conv)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.total_tool_calls, 1)
        self.assertEqual(trace.tool_calls[0].output_summary, "")


class TestRecoveryTraceToMarkdown(unittest.TestCase):
    """Test markdown formatting of recovery traces."""

    def test_none_returns_empty(self):
        self.assertEqual(recovery_trace_to_markdown(None), "")

    def test_basic_formatting(self):
        trace = RecoveryTrace(
            agent_role="dev",
            total_tool_calls=2,
            errors_count=1,
            input_tokens=5000,
            output_tokens=2000,
            tool_calls=[
                ToolCallTrace(tool_name="terminal", input_summary="git status", output_summary="clean"),
                ToolCallTrace(tool_name="file_editor", input_summary="view main.py", error=True),
            ],
        )
        md = recovery_trace_to_markdown(trace)
        self.assertIn("## Previous Execution Log", md)
        self.assertIn("**Agent:** dev", md)
        self.assertIn("**Tool Calls:** 2", md)
        self.assertIn("**Errors:** 1", md)
        self.assertIn("git status", md)
        self.assertIn("❌", md)

    def test_max_length_truncation(self):
        traces = [
            ToolCallTrace(
                tool_name="terminal",
                input_summary=f"command {i}",
                output_summary="x" * 500,
            )
            for i in range(50)
        ]
        trace = RecoveryTrace(
            agent_role="dev",
            total_tool_calls=50,
            errors_count=0,
            tool_calls=traces,
        )
        md = recovery_trace_to_markdown(trace, max_length=100)
        self.assertIn("(execution trace truncated)", md)
        self.assertLessEqual(len(md), 150)  # max_length + newline + truncation suffix


if __name__ == "__main__":
    unittest.main()
