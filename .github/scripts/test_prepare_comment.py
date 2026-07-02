"""Tests for prepare_comment module."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from prepare_comment import (
    clean_message,
    extract_metrics,
    _truncate_at_boundary,
    prepare_comment,
)


class TestExtractMetrics(unittest.TestCase):
    """Test metrics comment extraction."""

    def test_extract_single_line_metrics(self):
        content = "Hello\n\n<!-- agent-metrics: {\"key\": \"value\"} -->"
        result = extract_metrics(content)
        self.assertIn("agent-metrics", result)
        self.assertIn("key", result)

    def test_extract_multiline_metrics(self):
        content = (
            "Hello\n\n"
            "<!-- agent-metrics: {\n"
            '  "key": "value"\n'
            "} -->"
        )
        result = extract_metrics(content)
        self.assertIn("agent-metrics", result)

    def test_no_metrics_returns_empty(self):
        result = extract_metrics("Just plain text")
        self.assertEqual(result, "")

    def test_empty_input(self):
        result = extract_metrics("")
        self.assertEqual(result, "")


class TestCleanMessage(unittest.TestCase):
    """Test internal tag stripping."""

    def test_strips_agent_run_config(self):
        raw = (
            "Here is my response.\n\n"
            "<agentRunConfig>\n"
            '{"dispatch": {"status": "advance"}, "complexity": "low"}\n'
            "</agentRunConfig>"
        )
        result = clean_message(raw, "")
        self.assertNotIn("<agentRunConfig>", result)
        self.assertIn("Here is my response", result)

    def test_strips_multiple_agent_run_configs(self):
        raw = (
            "First part\n"
            "<agentRunConfig>{\"task\": \"genius\"}</agentRunConfig>\n"
            "Middle part\n"
            "<agentRunConfig>{\"task\": \"dev\"}</agentRunConfig>\n"
            "Last part"
        )
        result = clean_message(raw, "")
        self.assertNotIn("<agentRunConfig>", result)
        self.assertIn("First part", result)
        self.assertIn("Middle part", result)
        self.assertIn("Last part", result)

    def test_preserves_content_before_config_tag(self):
        raw = "Important summary text.<agentRunConfig>{}</agentRunConfig>"
        result = clean_message(raw, "")
        self.assertIn("Important summary text", result)
        self.assertNotIn("<agentRunConfig>", result)

    def test_strips_split_plan(self):
        raw = "Intro<splitPlan>{\"tasks\": []}</splitPlan>Outro"
        result = clean_message(raw, "")
        self.assertNotIn("<splitPlan>", result)
        self.assertIn("Intro", result)

    def test_preserves_metrics_comment_at_end(self):
        raw = "Message body"
        metrics = '<!-- agent-metrics: {"run_id": "123"} -->'
        result = clean_message(raw, metrics)
        self.assertIn(metrics, result)
        # Metrics should be at the end
        self.assertTrue(result.endswith(metrics))

    def test_strips_inline_metrics_and_readds_at_end(self):
        raw = "Start\n\n<!-- agent-metrics: {\"k\": 1} -->\n\nMiddle"
        metrics = extract_metrics(raw)
        result = clean_message(raw, metrics)
        # The inline metrics should be gone from the middle
        self.assertNotIn("<!-- agent-metrics", result.replace("<!-- agent-metrics: {\"k\": 1} -->", "").replace("<!-- agent-metrics: {\"k\": 1} -->", ""))
        # But present at the end
        self.assertTrue(result.endswith("<!-- agent-metrics: {\"k\": 1} -->"))

    def test_cleans_residual_blank_lines(self):
        raw = "Para1\n\n\n\n\nPara2"
        result = clean_message(raw, "")
        # Should collapse excessive blank lines
        self.assertNotIn("\n\n\n", result)

    def test_handles_special_characters_in_config(self):
        raw = (
            "Response text with $variables and `backticks`.\n\n"
            "<agentRunConfig>\n"
            '{"dispatch": {"status": "advance"}, "complexity": "low"}\n'
            "</agentRunConfig>\n\n"
            "More text after config."
        )
        result = clean_message(raw, "")
        self.assertIn("$variables", result)
        self.assertIn("`backticks`", result)
        self.assertNotIn("<agentRunConfig>", result)
        self.assertIn("More text after config", result)

    def test_multiline_agent_run_config(self):
        raw = (
            "Start\n"
            "<agentRunConfig>\n"
            '{\n'
            '  "dispatch": {"status": "advance"},\n'
            '  "complexity": "low"\n'
            "}\n"
            "</agentRunConfig>\n"
            "End"
        )
        result = clean_message(raw, "")
        self.assertNotIn("<agentRunConfig>", result)
        self.assertNotIn("</agentRunConfig>", result)
        self.assertIn("Start", result)
        self.assertIn("End", result)


class TestTruncateAtBoundary(unittest.TestCase):
    """Test truncation helper."""

    def test_no_truncation_when_under_limit(self):
        text = "Short message"
        result = _truncate_at_boundary(text)
        self.assertEqual(result, text)

    def test_truncates_over_limit(self):
        text = "A" * 70000
        result = _truncate_at_boundary(text)
        self.assertLessEqual(len(result), 60000 + len("\n\n...*(comment truncated due to size limit)*"))
        self.assertIn("truncated due to size limit", result)

    def test_prefers_paragraph_boundary(self):
        text = "First para\n\n" + "B" * 40000 + "\n\n" + "C" * 20000
        result = _truncate_at_boundary(text)
        self.assertIn("truncated due to size limit", result)

    def test_exact_limit_no_truncation(self):
        text = "X" * 60000
        result = _truncate_at_boundary(text)
        self.assertEqual(result, text)


class TestPrepareCommentIntegration(unittest.TestCase):
    """Integration tests for prepare_comment() using temp files."""

    def setUp(self):
        self._cwd = os.getcwd()
        self.tmpdir = tempfile.mkdtemp()
        os.chdir(self.tmpdir)
        # Clear env vars that the script reads
        self._saved_github_env = os.environ.pop("GITHUB_ENV", None)
        self._saved_trigger = os.environ.pop("TRIGGER_AUTHOR", None)

    def tearDown(self):
        os.chdir(self._cwd)
        if os.path.exists(self.tmpdir):
            for f in os.listdir(self.tmpdir):
                os.remove(os.path.join(self.tmpdir, f))
            os.rmdir(self.tmpdir)
        if self._saved_github_env:
            os.environ["GITHUB_ENV"] = self._saved_github_env
        if self._saved_trigger:
            os.environ["TRIGGER_AUTHOR"] = self._saved_trigger

    def _write(self, name, content):
        with open(name, "w", encoding="utf-8") as f:
            f.write(content)

    def _read(self, name):
        with open(name, "r", encoding="utf-8") as f:
            return f.read()

    def test_path_a_normal_message(self):
        self._write("final_message.txt", "Hello world, this is the agent response.")
        prepare_comment()
        self.assertTrue(os.path.isfile("github_comment.md"))
        self.assertIn("Hello world", self._read("github_comment.md"))

    def test_path_a_strips_agent_run_config(self):
        content = (
            "Agent completed the task.\n\n"
            "<agentRunConfig>\n"
            '{"dispatch": {"status": "advance"}}\n'
            "</agentRunConfig>"
        )
        self._write("final_message.txt", content)
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("Agent completed the task", result)
        self.assertNotIn("<agentRunConfig>", result)

    def test_path_a_preserves_metrics(self):
        content = (
            "Summary text.\n\n"
            "<!-- agent-metrics: {\"run_id\": \"abc\"} -->"
        )
        self._write("final_message.txt", content)
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("Summary text", result)
        self.assertIn("agent-metrics", result)

    def test_path_a_sentinel_falls_back_to_console_log(self):
        self._write("final_message.txt", "__NO_FINAL_MESSAGE__ no data")
        self._write("console.log",
                     "Message from Agent " + "\u2500" * 63 + "\nFallback message content\nTokens: 100")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("Fallback message content", result)

    def test_path_d_no_final_message_file(self):
        # No final_message.txt at all
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("did not produce a final message", result)

    def test_path_a_sets_has_comment(self):
        os.environ["GITHUB_ENV"] = os.path.join(self.tmpdir, "env.txt")
        self._write("final_message.txt", "Test content")
        prepare_comment()
        self.assertTrue(os.path.isfile("env.txt"))
        self.assertIn("HAS_COMMENT=true", self._read("env.txt"))

    def test_path_a_empty_after_cleaning_falls_back(self):
        # Message only contains tags — empty after cleaning
        content = "<agentRunConfig>{}</agentRunConfig>"
        self._write("final_message.txt", content)
        self._write("console.log", "Some console output line")
        prepare_comment()
        # Should have produced some output via fallback
        self.assertTrue(os.path.isfile("github_comment.md"))

    def test_multiline_agent_run_config_not_truncated(self):
        """Regression test: multi-line agentRunConfig should not cause truncation."""
        content = (
            "Here is the detailed implementation summary.\n\n"
            "I have completed the following:\n"
            "1. Created the new module\n"
            "2. Updated the test suite\n"
            "3. Fixed integration issues\n\n"
            "The code is ready for review.\n\n"
            "<agentRunConfig>\n"
            '{\n'
            '  "dispatch": {"status": "advance"},\n'
            '  "complexity": "low"\n'
            "}\n"
            "</agentRunConfig>"
        )
        self._write("final_message.txt", content)
        prepare_comment()
        result = self._read("github_comment.md")
        # All text before config should be preserved
        self.assertIn("Here is the detailed implementation summary", result)
        self.assertIn("Created the new module", result)
        self.assertIn("Updated the test suite", result)
        self.assertIn("Fixed integration issues", result)
        self.assertIn("ready for review", result)
        # Config tag should be stripped
        self.assertNotIn("<agentRunConfig>", result)
        self.assertNotIn("dispatch", result)

    def test_content_with_special_chars(self):
        """Content with $, backticks, and other shell-special chars should pass through."""
        content = (
            "Use `$ENV_VAR` to configure.\n\n"
            "```\n"
            "echo `date`\n"
            "```\n\n"
            "See <https://example.com> for docs."
        )
        self._write("final_message.txt", content)
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("$ENV_VAR", result)
        self.assertIn("`date`", result)
        self.assertIn("<https://example.com>", result)


if __name__ == "__main__":
    unittest.main()
