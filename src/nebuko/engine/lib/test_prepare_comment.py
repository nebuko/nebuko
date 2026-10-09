"""Tests for prepare_comment module."""

import json
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
    WORKFLOW_STATE_FILE,
    _truncate_at_boundary,
    _write_output,
    build_comment_body,
    clean_message,
    escape_agent_triggers,
    merge_handoff_sentinels,
    neutralize_human_pings,
    prepare_comment,
    render_handoff_sentinel,
    select_posted_body,
)

# ``build_comment_body`` prepends the answer-comment header (issue #736); the
# body assertions below compare against the headerless body.
_HEADER_TERMINATOR = " answer*\n\n"


def _strip_answer_header(comment: str) -> str:
    """Return ``comment`` with the answer-comment header removed (issue #736)."""
    marker = comment.find(_HEADER_TERMINATOR)
    return comment[marker + len(_HEADER_TERMINATOR):] if marker != -1 else comment


class TestCleanMessage(unittest.TestCase):
    """Test internal tag stripping."""

    def test_strips_new_sentinel(self):
        raw = (
            "Here is my response.\n\n"
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "low"} -->'
        )
        result = clean_message(raw)
        self.assertNotIn("AGENT_RUN_CONFIG", result)
        self.assertIn("Here is my response", result)

    def test_strips_truncated_new_sentinel(self):
        raw = (
            "Here is my response.\n\n"
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"statu'
        )
        result = clean_message(raw)
        self.assertNotIn("AGENT_RUN_CONFIG", result)
        self.assertIn("Here is my response", result)

    def test_strips_agent_run_config(self):
        raw = (
            "Here is my response.\n\n"
            "<agentRunConfig>\n"
            '{"dispatch": {"status": "advance"}, "complexity": "low"}\n'
            "</agentRunConfig>"
        )
        result = clean_message(raw)
        self.assertNotIn("<agentRunConfig>", result)
        self.assertIn("Here is my response", result)

    def test_strips_multiple_agent_run_configs(self):
        raw = (
            "First part\n"
            "<agentRunConfig>{\"task\": \"architect\"}</agentRunConfig>\n"
            "Middle part\n"
            "<agentRunConfig>{\"task\": \"dev\"}</agentRunConfig>\n"
            "Last part"
        )
        result = clean_message(raw)
        self.assertNotIn("<agentRunConfig>", result)
        self.assertIn("First part", result)
        self.assertIn("Middle part", result)
        self.assertIn("Last part", result)

    def test_preserves_content_before_config_tag(self):
        raw = "Important summary text.<agentRunConfig>{}</agentRunConfig>"
        result = clean_message(raw)
        self.assertIn("Important summary text", result)
        self.assertNotIn("<agentRunConfig>", result)

    def test_strips_split_plan(self):
        raw = "Intro<splitPlan>{\"tasks\": []}</splitPlan>Outro"
        result = clean_message(raw)
        self.assertNotIn("<splitPlan>", result)
        self.assertIn("Intro", result)

    def test_cleans_residual_blank_lines(self):
        raw = "Para1\n\n\n\n\nPara2"
        result = clean_message(raw)
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
        result = clean_message(raw)
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
        result = clean_message(raw)
        self.assertNotIn("<agentRunConfig>", result)
        self.assertNotIn("</agentRunConfig>", result)
        self.assertIn("Start", result)
        self.assertIn("End", result)


class TestEscapeAgentTriggers(unittest.TestCase):
    """Test @@agent trigger escaping (issue #482)."""

    def test_escapes_single_trigger(self):
        result = escape_agent_triggers("Hello @@architect thanks")
        self.assertIn("@@\u200barchitect", result)
        self.assertNotIn("hello @@architect".replace("h", "H"), result)

    def test_no_raw_trigger_after_escape(self):
        result = escape_agent_triggers("Ping @@architect and @@dev now")
        self.assertNotIn("@@architect", result)
        self.assertNotIn("@@dev", result)
        self.assertIn("@@\u200barchitect", result)
        self.assertIn("@@\u200bdev", result)

    def test_escapes_multiple_tokens(self):
        result = escape_agent_triggers("@@context then @@dev then @@review")
        self.assertNotIn("@@context", result)
        self.assertNotIn("@@dev", result)
        self.assertNotIn("@@review", result)

    def test_escapes_at_start_of_string(self):
        result = escape_agent_triggers("@@architect start")
        self.assertEqual(result, "@@\u200barchitect start")

    def test_does_not_escape_mid_word(self):
        # Only escape when @@ is preceded by start/whitespace (lookbehind rule)
        result = escape_agent_triggers("x@@architect is a mid-word occurrence")
        self.assertEqual(result, "x@@architect is a mid-word occurrence")

    def test_no_tokens_unchanged(self):
        text = "No triggers here, just plain @mentions and @@ without name."
        self.assertEqual(escape_agent_triggers(text), text)

    def test_empty_input(self):
        self.assertEqual(escape_agent_triggers(""), "")


class TestNeutralizeHumanPings(unittest.TestCase):
    """Agent-authored bodies never carry a live human ping (issue #795)."""

    def test_neutralizes_a_single_mention(self):
        result = neutralize_human_pings("@olivier-norture ready for review")
        self.assertIn("@\u200bolivier-norture", result)
        self.assertNotIn("@olivier-norture", result)

    def test_neutralizes_multiple_mentions(self):
        result = neutralize_human_pings("cc @alice and @bob-c please")
        self.assertIn("@\u200balice", result)
        self.assertIn("@\u200bbob-c", result)
        self.assertNotIn("@alice", result)
        self.assertNotIn("@bob-c", result)

    def test_keeps_the_login_readable(self):
        self.assertIn("olivier-norture", neutralize_human_pings("@olivier-norture"))

    def test_defuses_with_a_zero_width_space(self):
        result = neutralize_human_pings("@olivier-norture")
        self.assertIn("\u200b", result)
        self.assertEqual(result, "@\u200bolivier-norture")

    def test_leaves_agent_triggers_to_escape_agent_triggers(self):
        # ``@@`` tokens are owned by ``escape_agent_triggers``; this helper must
        # not touch them so the two transformations stay independently testable.
        text = "ping @@architect and @@dev now"
        self.assertEqual(neutralize_human_pings(text), text)

    def test_no_mentions_unchanged(self):
        text = "plain text without any mentions"
        self.assertEqual(neutralize_human_pings(text), text)

    def test_empty_input(self):
        self.assertEqual(neutralize_human_pings(""), "")

    def test_leaves_a_fenced_code_block_verbatim(self):
        """A mention in a fence never notifies; de-fanging it would mangle it."""
        text = "See:\n```python\n@decorator\ndef f():\n    return '@author'\n```\ncc @alice"
        result = neutralize_human_pings(text)
        self.assertIn("@decorator", result)
        self.assertIn("'@author'", result)
        self.assertIn("@\u200balice", result)

    def test_leaves_inline_code_verbatim(self):
        result = neutralize_human_pings("use `@dataclass` then ping @alice")
        self.assertIn("`@dataclass`", result)
        self.assertIn("@\u200balice", result)

    def test_leaves_a_word_glued_mention_verbatim(self):
        """``user@host`` is not a GitHub notification, so it stays readable."""
        text = "mail user@host.example to report it"
        self.assertEqual(neutralize_human_pings(text), text)

    def test_defuses_a_mention_at_the_start_of_a_line(self):
        result = neutralize_human_pings("@alice please review")
        self.assertEqual(result, "@\u200balice please review")


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

    def test_path_a_has_no_metrics_block(self):
        # Metrics no longer live in a hidden HTML comment (issue #792): the
        # posted body carries only the answer text.
        self._write("final_message.txt", "Summary text with no hidden payload.")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("Summary text", result)
        self.assertNotIn("agent-metrics", result)

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

    def test_path_a_neutralizes_human_pings(self):
        """An agent-authored body never carries a live human ping (issue #795)."""
        self._write("final_message.txt", "@olivier-norture the PR is ready for review.")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("@\u200bolivier-norture", result)
        self.assertNotIn("@olivier-norture", result)

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

    # --- issue #830: engine-owned, deterministic hand-off sentinel ---

    def test_typed_finish_report_beats_the_one_line_lead_in(self):
        """#822 end-to-end: the full report is posted, not the turn-text lead-in.

        ``build_output`` captures ``finish.message`` as ``latest_answer.md``
        (#822): the wind-down one-liner the model wrote as turn text must not
        win over the report it typed into the finish argument.
        """
        report = "## feat(#822): nested step addressing via dotted StepPath\n\nFull body."
        self._write(LATEST_ANSWER_FILE, report)
        self._write(
            "final_message.txt", "Work pushed and PR updated. Here is my wind-down summary."
        )
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn("Full body.", result)
        self.assertNotIn("wind-down summary", result)

    def test_engine_renders_the_sentinel_from_the_typed_state(self):
        self._write(
            WORKFLOW_STATE_FILE,
            json.dumps({"dispatch": {"status": "advance", "complexity": "high"}}),
        )
        self._write(LATEST_ANSWER_FILE, "Full report body")
        self._write("final_message.txt", "Full report body")
        prepare_comment()
        result = self._read("github_comment.md")
        block = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "complexity": "high"}} -->'
        self.assertIn(block, result)
        self.assertEqual(result.count("AGENT_RUN_CONFIG"), 1)

    def test_engine_sentinel_wins_over_the_scraped_prose(self):
        prose = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "loop"}} -->'
        self._write(WORKFLOW_STATE_FILE, json.dumps({"dispatch": {"status": "hold"}}))
        self._write(LATEST_ANSWER_FILE, "Full report\n\n" + prose)
        self._write("final_message.txt", "Status note")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertEqual(result.count("AGENT_RUN_CONFIG"), 1)
        self.assertIn('"status": "hold"', result)
        self.assertNotIn("loop", result)

    def test_scraped_sentinel_still_used_without_a_typed_state(self):
        prose = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "loop"}} -->'
        self._write(LATEST_ANSWER_FILE, "Full report\n\n" + prose)
        self._write("final_message.txt", "Status note")
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertIn(prose, result)


class TestPrepareCommentContextCollapse(TestPrepareCommentIntegration):
    """context-role reports get sections collapsed into <details> (issue #557)."""

    CONTEXT_REPORT = (
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

    def test_context_report_sections_collapsed(self):
        os.environ["ROLE_NAME"] = "context"
        self._write("final_message.txt", self.CONTEXT_REPORT)
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

    def test_non_context_report_untouched(self):
        os.environ["ROLE_NAME"] = "dev"
        self._write("final_message.txt", self.CONTEXT_REPORT)
        prepare_comment()
        result = self._read("github_comment.md")
        self.assertNotIn("<details>", result)
        self.assertIn("### FILES FOUND", result)

    def test_context_body_without_sections_passes_through(self):
        os.environ["ROLE_NAME"] = "context"
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

    def test_carries_the_continue_token_for_a_soft_kill(self):
        """Issue #793: a soft-kill comment must re-arm the engine's own loop.

        The soft-kill conclusion comment is the engine's own trigger, so it
        must carry the continue token; the next run's
        ``resolve_soft_kill_route`` then retries the interrupted step in place
        (bounded by ``MAX_SOFT_RETRIES``) instead of the workflow stopping.
        """
        self.assertIn(self.CONTINUE_TOKEN, self._body(status="soft_kill"))

    def test_keeps_the_header_but_omits_the_section_without_a_state(self):
        body = self._body(state=None)

        self.assertEqual(body.splitlines()[0], self.BOT_MARKER)
        self.assertIn("<!-- bot-agent-dev -->", body)
        self.assertIn(self.CONTINUE_TOKEN, body)
        self.assertNotIn("<!-- WORKFLOW_STATE_START -->", body)
        self.assertIn(self.ANSWER, body)


class TestSoftKillReproduction(unittest.TestCase):
    """Reproduction for issue #793 from the captured soft-kill state blob.

    The fixture is the exact state the issue reports: ``step=implement``,
    ``agent=dev``, ``dispatch.status=soft_kill`` and
    ``current_context.last_run_status=gracefully_timed_out``. The soft-killed
    run posted its conclusion comment and nothing restarted it, because the
    published body carried no continue token. The engine's own conclusion
    comment is its trigger, so it must now carry the token.
    """

    FIXTURE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "fixtures",
        "workflow_state_issue793_soft_kill.json",
    )

    @staticmethod
    def _state():
        from workflow_state.workflow_state import WorkflowState

        with open(TestSoftKillReproduction.FIXTURE, encoding="utf-8") as handle:
            return WorkflowState.model_validate(json.load(handle))

    def test_captured_soft_kill_state_rearms_the_engine(self):
        state = self._state()
        # Guard the fixture still captures the reported condition, otherwise
        # this test could pass for the wrong reason.
        self.assertEqual(state.step, "implement")
        self.assertEqual(state.agent, "dev")
        self.assertEqual(state.dispatch.status, "soft_kill")
        self.assertEqual(state.current_context.get("last_run_status"), "gracefully_timed_out")

        body = build_comment_body(
            latest_answer="Soft time-kill: the step will be retried.",
            final_message="Soft time-kill: the step will be retried.",
            role_name="dev",
            state=state,
        )

        self.assertIn("<!-- workflow-continue -->", body)


class TestRenderHandoffSentinel(unittest.TestCase):
    """Issue #830: the engine renders the hand-off sentinel from typed state.

    The posted sentinel is derived from ``workflow_state.dispatch`` /
    ``function_call`` rather than parsed out of agent prose, so retrieval of the
    routing decision no longer depends on the model reproducing a sentinel.
    """

    @staticmethod
    def _state(dispatch=None, function_call=None):
        from workflow_state.workflow_state import WorkflowState

        return WorkflowState(dispatch=dispatch, function_call=function_call)

    @staticmethod
    def _payload(sentinel):
        marker = "AGENT_RUN_CONFIG "
        start = sentinel.index(marker) + len(marker)
        return json.loads(sentinel[start:sentinel.rindex(" -->")])

    def test_no_state_renders_nothing(self):
        self.assertEqual(render_handoff_sentinel(None), "")

    def test_state_without_a_decision_renders_nothing(self):
        self.assertEqual(render_handoff_sentinel(self._state()), "")

    def test_dispatch_is_rendered_from_typed_fields(self):
        from workflow_state.sub_domains.dispatch.dispatch import Dispatch

        state = self._state(dispatch=Dispatch(status="loop", branch="rejected", reason="gate"))
        dispatch = self._payload(render_handoff_sentinel(state))["dispatch"]

        self.assertEqual(dispatch["status"], "loop")
        self.assertEqual(dispatch["branch"], "rejected")
        self.assertEqual(dispatch["reason"], "gate")
        # Unset/blank fields are omitted, keeping the payload minimal.
        self.assertNotIn("complexity", dispatch)

    def test_function_call_is_rendered_alongside_dispatch(self):
        from workflow_state.sub_domains.dispatch.dispatch import Dispatch
        from workflow_state.sub_domains.function_call.function_call import FunctionCall

        state = self._state(
            dispatch=Dispatch(status="advance"),
            function_call=FunctionCall(
                function="call_agent", agent_name="review", callback="dev"
            ),
        )
        payload = self._payload(render_handoff_sentinel(state))

        self.assertEqual(payload["dispatch"], {"status": "advance"})
        self.assertEqual(payload["function_call"]["agent_name"], "review")
        self.assertEqual(payload["function_call"]["callback"], "dev")

    def test_rendered_sentinel_is_a_single_well_formed_block(self):
        from workflow_state.sub_domains.dispatch.dispatch import Dispatch

        sentinel = render_handoff_sentinel(self._state(dispatch=Dispatch(status="advance")))

        self.assertTrue(sentinel.startswith("<!-- AGENT_RUN_CONFIG "))
        self.assertTrue(sentinel.endswith(" -->"))
        self.assertEqual(sentinel.count("AGENT_RUN_CONFIG"), 1)


if __name__ == "__main__":
    unittest.main()
