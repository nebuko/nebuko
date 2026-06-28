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
