"""Tests for the comment classifier (issue #872)."""

import os
import sys


from nebuko.engine.lib.workflow_state.features.classify_comment import classify_comment
from nebuko.engine.lib.workflow_state.sub_domains.thread_record import ThreadRecordKind

HUMAN_ACTION = "<!-- bot-human-action -->\n**human action needed**"
AGENT_STARTED = (
    "<!-- bot-agent-dev -->\n**dev agent started** — "
    "[View workflow run logs](https://example.test/runs/1)"
)
RUNNER_ERROR = "The agent crashed or timed out before producing output"
NO_FINAL_MESSAGE = "The agent did not produce a final message"
AGENT_ANSWER = "<!-- bot-agent-architect -->\n*Architect answer*\n\nPlan attached."
COMMAND = "/nebuko approve gate"
MENTION = "@@architect please update the plan"
CHATTER = "I think we should use a tuple here."


class TestClassifyEngineNotices:
    def test_human_action_marker_is_engine_output(self):
        assert (
            classify_comment(HUMAN_ACTION, author_is_bot=True)
            is ThreadRecordKind.ENGINE_OUTPUT
        )

    def test_agent_started_notice_is_engine_output(self):
        # The acknowledgment carries both the start line and a bot-agent marker;
        # the engine classification must win over the generic agent marker.
        assert (
            classify_comment(AGENT_STARTED, author_is_bot=True)
            is ThreadRecordKind.ENGINE_OUTPUT
        )

    def test_runner_error_fallbacks_are_engine_output(self):
        for text in (RUNNER_ERROR, NO_FINAL_MESSAGE):
            assert (
                classify_comment(text, author_is_bot=True)
                is ThreadRecordKind.ENGINE_OUTPUT
            )

    def test_bot_without_a_marker_is_engine_output(self):
        assert (
            classify_comment("plain bot note", author_is_bot=True)
            is ThreadRecordKind.ENGINE_OUTPUT
        )


class TestClassifyAgentOutput:
    def test_agent_marker_is_agent_output(self):
        assert (
            classify_comment(AGENT_ANSWER, author_is_bot=True)
            is ThreadRecordKind.AGENT_OUTPUT
        )

    def test_agent_marker_wins_over_bot_default(self):
        assert (
            classify_comment("<!-- bot-agent-review -->\nLGTM", author_is_bot=True)
            is ThreadRecordKind.AGENT_OUTPUT
        )


class TestClassifyHuman:
    def test_slash_command_is_a_command(self):
        assert (
            classify_comment(COMMAND, author_is_bot=False)
            is ThreadRecordKind.COMMAND
        )

    def test_command_with_leading_whitespace(self):
        assert (
            classify_comment("   /nebuko retry", author_is_bot=False)
            is ThreadRecordKind.COMMAND
        )

    def test_agent_mention_is_a_command(self):
        assert (
            classify_comment(MENTION, author_is_bot=False)
            is ThreadRecordKind.COMMAND
        )

    def test_plain_human_text_is_feedback(self):
        assert (
            classify_comment(CHATTER, author_is_bot=False)
            is ThreadRecordKind.HUMAN_FEEDBACK
        )

    def test_empty_body_is_feedback(self):
        assert (
            classify_comment("", author_is_bot=False)
            is ThreadRecordKind.HUMAN_FEEDBACK
        )

    def test_a_mid_line_slash_is_not_a_command(self):
        assert (
            classify_comment("use the /foo flag", author_is_bot=False)
            is ThreadRecordKind.HUMAN_FEEDBACK
        )


class TestClassifyUntrustedText:
    """A non-bot author is never classified from bot markers in its text (#872)."""

    def test_a_quoted_agent_marker_is_human_feedback(self):
        # GitHub quote-reply copies ``<!-- bot-agent-dev -->`` into the quote.
        assert (
            classify_comment(
                "> <!-- bot-agent-dev -->\n> the dev answer", author_is_bot=False
            )
            is ThreadRecordKind.HUMAN_FEEDBACK
        )

    def test_a_quoted_engine_notice_is_human_feedback(self):
        assert (
            classify_comment(AGENT_STARTED, author_is_bot=False)
            is ThreadRecordKind.HUMAN_FEEDBACK
        )

    def test_a_quoted_runner_error_is_human_feedback(self):
        assert (
            classify_comment(RUNNER_ERROR, author_is_bot=False)
            is ThreadRecordKind.HUMAN_FEEDBACK
        )

    def test_the_same_body_is_bot_output_for_a_bot_author(self):
        # The gate is the author, not the text: the identical body stays a
        # bot-derived kind when a bot actually authored it.
        assert (
            classify_comment(AGENT_ANSWER, author_is_bot=True)
            is ThreadRecordKind.AGENT_OUTPUT
        )
        assert (
            classify_comment(AGENT_STARTED, author_is_bot=True)
            is ThreadRecordKind.ENGINE_OUTPUT
        )
