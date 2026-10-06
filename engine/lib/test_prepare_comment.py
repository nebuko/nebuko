"""Tests for prepare_comment module."""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _root in (os.path.dirname(__file__), REPO_ROOT):
    if _root not in sys.path:
        sys.path.insert(0, _root)
from prepare_comment import (
    LATEST_ANSWER_FILE,
    _truncate_at_boundary,
    _write_output,
    build_comment_body,
    clean_message,
    escape_agent_triggers,
    extract_metrics,
    merge_handoff_sentinels,
    prepare_comment,
    select_posted_body,
)

# ``build_comment_body`` prepends the answer-comment header (issue #736); the
# body assertions below compare against the headerless body.
_HEADER_TERMINATOR = " answer*\n\n"


def _strip_answer_header(comment: str) -> str:
    """Return ``comment`` with the answer-comment header removed (issue #736)."""
    marker = comment.find(_HEADER_TERMINATOR)
    return comment[marker + len(_HEADER_TERMINATOR):] if marker != -1 else comment


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

    def test_strips_new_sentinel(self):
        raw = (
            "Here is my response.\n\n"
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "low"} -->'
        )
        result = clean_message(raw, "")
        self.assertNotIn("AGENT_RUN_CONFIG", result)
        self.assertIn("Here is my response", result)

    def test_strips_truncated_new_sentinel(self):
        raw = (
            "Here is my response.\n\n"
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"statu'
        )
        result = clean_message(raw, "")
        self.assertNotIn("AGENT_RUN_CONFIG", result)
        self.assertIn("Here is my response", result)

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


class TestEscapeAgentTriggers(unittest.TestCase):
    """Test @@agent trigger escaping (issue #482)."""

    def test_escapes_single_trigger(self):
        result = escape_agent_triggers("Hello @@genius thanks")
        self.assertIn("@@\u200bgenius", result)
        self.assertNotIn("hello @@genius".replace("h", "H"), result)

    def test_no_raw_trigger_after_escape(self):
        result = escape_agent_triggers("Ping @@genius and @@dev now")
        self.assertNotIn("@@genius", result)
        self.assertNotIn("@@dev", result)
        self.assertIn("@@\u200bgenius", result)
        self.assertIn("@@\u200bdev", result)

    def test_escapes_multiple_tokens(self):
        result = escape_agent_triggers("@@ctx then @@dev then @@review")
        self.assertNotIn("@@ctx", result)
        self.assertNotIn("@@dev", result)
        self.assertNotIn("@@review", result)

    def test_escapes_at_start_of_string(self):
        result = escape_agent_triggers("@@genius start")
        self.assertEqual(result, "@@\u200bgenius start")

    def test_does_not_escape_mid_word(self):
        # Only escape when @@ is preceded by start/whitespace (lookbehind rule)
        result = escape_agent_triggers("x@@genius is a mid-word occurrence")
        self.assertEqual(result, "x@@genius is a mid-word occurrence")

    def test_no_tokens_unchanged(self):
        text = "No triggers here, just plain @mentions and @@ without name."
        self.assertEqual(escape_agent_triggers(text), text)

    def test_empty_input(self):
        self.assertEqual(escape_agent_triggers(""), "")


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

    def test_path_a_escapes_agent_triggers(self):
        """Raw @@ triggers in the final message must arrive escaped (issue #482)."""
        self._write("final_message.txt", "Done. Handing off to @@dev next.")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("@@\u200bdev", result)
        self.assertNotIn("handing off to @@dev", result)
        self.assertNotIn("to @@dev", result)

    def test_path_d_crash_fallback_sets_has_comment(self):
        """Empty/no final message + empty console.log still sets HAS_COMMENT=true."""
        os.environ["GITHUB_ENV"] = os.path.join(self.tmpdir, "env.txt")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("did not produce a final message", result)
        self.assertTrue(os.path.isfile("env.txt"))
        self.assertIn("HAS_COMMENT=true", self._read("env.txt"))


class TestSelectPostedBody(unittest.TestCase):
    """ETL layer: answer-first selection with finish-message fallback (issue #522)."""

    def test_latest_answer_wins(self):
        body = select_posted_body("# Full report\nbody", "short status")
        self.assertEqual(body, "# Full report\nbody")

    def test_missing_answer_falls_back_to_finish(self):
        body = select_posted_body("", "short status")
        self.assertEqual(body, "short status")

    def test_blank_answer_falls_back_to_finish(self):
        body = select_posted_body("   \n\t ", "short status")
        self.assertEqual(body, "short status")

    def test_both_empty_returns_empty(self):
        self.assertEqual(select_posted_body("", ""), "")


class TestMergeHandoffSentinels(unittest.TestCase):
    """ETL layer: AGENT_RUN_CONFIG preserved exactly once, last one wins."""

    SENTINEL = '{"dispatch": {"status": "advance"}}'

    def _block(self, payload):
        return f"<!-- AGENT_RUN_CONFIG {payload} -->"

    def test_no_sources_returns_empty(self):
        self.assertEqual(merge_handoff_sentinels(), "")
        self.assertEqual(merge_handoff_sentinels("", "", None), "")

    def test_sentinel_in_answer_only(self):
        answer = "report\n\n" + self._block(self.SENTINEL)
        result = merge_handoff_sentinels(answer, "status without sentinel")
        self.assertEqual(result, self._block(self.SENTINEL))
        self.assertEqual(result.count("AGENT_RUN_CONFIG"), 1)

    def test_sentinel_in_finish_only(self):
        result = merge_handoff_sentinels("plain answer", "status\n" + self._block(self.SENTINEL))
        self.assertEqual(result, self._block(self.SENTINEL))

    def test_last_wins_when_in_both(self):
        first = self._block('{"dispatch": {"status": "loop"}}')
        last = self._block(self.SENTINEL)
        result = merge_handoff_sentinels("answer " + first, "finish " + last)
        self.assertEqual(result, last)
        self.assertNotIn("loop", result)


class TestPrepareCommentLatestAnswerETL(TestPrepareCommentIntegration):
    """Integration: prepare_comment() consumes latest_answer.md (issue #522)."""

    def test_latest_answer_used_as_posted_body(self):
        self._write(LATEST_ANSWER_FILE, "# Deliverable report\nfull content here")
        self._write("final_message.txt", "Short finish status only")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("# Deliverable report", result)
        self.assertIn("full content here", result)
        self.assertNotIn("Short finish status only", result)

    def test_empty_latest_answer_falls_back_to_finish(self):
        self._write(LATEST_ANSWER_FILE, "   ")
        self._write("final_message.txt", "Legacy finish body")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("Legacy finish body", result)

    def test_missing_latest_answer_falls_back_to_finish(self):
        self._write("final_message.txt", "Legacy finish body")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("Legacy finish body", result)

    def test_sentinel_appended_exactly_once_when_in_both(self):
        block = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        self._write(LATEST_ANSWER_FILE, "Full report\n\n" + block)
        self._write("final_message.txt", "Status note\n\n" + block)
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertEqual(result.count(block), 1)

    def test_sentinel_from_finish_preserved_when_answer_lacks_it(self):
        block = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        self._write(LATEST_ANSWER_FILE, "Full report without sentinel")
        self._write("final_message.txt", "Status note\n\n" + block)
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn(block, result)
        self.assertEqual(result.count(block), 1)


class TestPrepareCommentCtxCollapse(TestPrepareCommentIntegration):
    """ctx-role reports get sections collapsed into <details> (issue #557)."""

    CTX_REPORT = (
        "## 🔍 Context Collection Report — posting pipeline\n\n"
        "Exec summary line one.\nExec summary line two.\n\n"
        "### FILES FOUND\n"
        "*3 files covering the posting pipeline.*\n"
        "a.py | 1-10\n\n"
        "### RELEVANT SNIPPETS\n"
        "*2 snippets.*\n"
        "```python\ndef foo(): pass\n```\n\n"
        "### EXTERNAL REFERENCES\n"
        "*No external refs.*\n\n"
        "### OTHER FILES\n"
        "*Nothing else.*\n"
    )

    def setUp(self):
        super().setUp()
        self._saved_role = os.environ.pop("ROLE_NAME", None)

    def tearDown(self):
        if self._saved_role is not None:
            os.environ["ROLE_NAME"] = self._saved_role
        super().tearDown()

    def test_ctx_report_sections_collapsed(self):
        os.environ["ROLE_NAME"] = "ctx"
        self._write("final_message.txt", self.CTX_REPORT)
        prepare_comment()
        result = _strip_answer_header(self._read("github_comment.md"))
        self.assertEqual(result.count("<details>"), 4)
        self.assertIn(
            "<summary>FILES FOUND — 3 files covering the posting pipeline.</summary>",
            result,
        )
        # Title + exec summary stay visible outside any details block
        self.assertTrue(result.startswith("## 🔍 Context Collection Report"))
        idx_summary = result.index("Exec summary line two.")
        idx_details = result.index("<details>")
        self.assertLess(idx_summary, idx_details)

    def test_non_ctx_report_untouched(self):
        os.environ["ROLE_NAME"] = "dev"
        self._write("final_message.txt", self.CTX_REPORT)
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertNotIn("<details>", result)
        self.assertIn("### FILES FOUND", result)

    def test_ctx_body_without_sections_passes_through(self):
        os.environ["ROLE_NAME"] = "ctx"
        plain = "Just a short answer, no sections."
        self._write("final_message.txt", plain)
        prepare_comment()
        self.assertEqual(_strip_answer_header(self._read("github_comment.md")), plain)


class TestBuildCommentBodyHeader(unittest.TestCase):
    """The agent-answer comment carries the workflow state (issue #736).

    The Stage B cutover dropped the legacy comment-assembly step.
    ``build_comment_body`` restores it on the *agent-answer* comment: the bot
    marker on line 1 (issue #478), the caller marker, a deterministic continue
    token while the flow stays alive, the collapsed state section, the role
    answer line and finally the answer text.
    """

    BOT_MARKER = "<!-- bot-workflow-terminal -->"
    CONTINUE_TOKEN = "<!-- workflow-continue -->\n"
    ANSWER = "Implemented the step."

    @staticmethod
    def _state(status=None, **overrides):
        from workflow_state.sub_domains.dispatch.dispatch import Dispatch
        from workflow_state.workflow_state import WorkflowState

        dispatch = None if status is None else Dispatch(status=status)
        return WorkflowState(
            version=7,
            workflow="spec-driven-impl",
            step="stub",
            agent="dev",
            dispatch=dispatch,
            **overrides,
        )

    def _body(self, *, state=..., status="advance", role_name="dev"):
        resolved = self._state(status) if state is ... else state
        return build_comment_body(
            latest_answer=self.ANSWER,
            final_message=self.ANSWER,
            role_name=role_name,
            state=resolved,
        )

    def test_puts_the_bot_marker_on_the_first_line(self):
        self.assertEqual(self._body().splitlines()[0], self.BOT_MARKER)

    def test_embeds_the_collapsed_state_section(self):
        from workflow_state.workflow_state import STATE_MARKER_END, STATE_MARKER_START

        body = self._body()

        self.assertIn(STATE_MARKER_START, body)
        self.assertIn(STATE_MARKER_END, body)
        self.assertIn('"step": "stub"', body)

    def test_orders_marker_token_state_role_line_and_answer(self):
        body = self._body()

        self.assertIn("<!-- bot-agent-dev -->", body)
        self.assertIn("*Developer answer*", body)
        self.assertLess(body.index("<!-- bot-agent-dev -->"), body.index(self.CONTINUE_TOKEN))
        self.assertLess(body.index(self.CONTINUE_TOKEN), body.index("*Developer answer*"))
        self.assertLess(body.index("*Developer answer*"), body.index(self.ANSWER))

    def test_carries_the_continue_token_for_an_advancing_run(self):
        self.assertIn(self.CONTINUE_TOKEN, self._body(status="advance"))

    def test_carries_the_continue_token_for_a_looping_run(self):
        self.assertIn(self.CONTINUE_TOKEN, self._body(status="loop"))

    def test_omits_the_token_when_the_flow_ends(self):
        self.assertNotIn(self.CONTINUE_TOKEN, self._body(status="hold"))

    def test_omits_the_token_for_a_terminate(self):
        self.assertNotIn(self.CONTINUE_TOKEN, self._body(status="terminate"))

    def test_omits_the_token_for_a_soft_kill(self):
        self.assertNotIn(self.CONTINUE_TOKEN, self._body(status="soft_kill"))

    def test_keeps_the_header_but_omits_the_section_without_a_state(self):
        body = self._body(state=None)

        self.assertEqual(body.splitlines()[0], self.BOT_MARKER)
        self.assertIn("<!-- bot-agent-dev -->", body)
        self.assertIn(self.CONTINUE_TOKEN, body)
        self.assertNotIn("<!-- WORKFLOW_STATE_START -->", body)
        self.assertIn(self.ANSWER, body)


if __name__ == "__main__":
    unittest.main()
