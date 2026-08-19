"""Tests for parse_trigger feature (pure string extraction)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.features.parse_trigger.parse_trigger import (  # noqa: E402
    build_trigger_commands,
    extract_debug_flag,
    extract_force_next_command,
    extract_mention_agent,
    extract_resume_command,
    extract_workflow_command,
    is_bot_comment,
)
from resolve_config.sub_domains.trigger_commands.trigger_commands import (  # noqa: E402
    TriggerCommands,
)


class TestIsBotComment:
    def test_bot_marker_in_first_lines(self):
        assert is_bot_comment("<!-- bot-agent-dev -->\nbody") is True

    def test_bot_marker_beyond_five_lines(self):
        assert is_bot_comment("a\nb\nc\nd\ne\n<!-- bot-agent-dev -->") is False

    def test_plain_body(self):
        assert is_bot_comment("hello world") is False


class TestExtractMentionAgent:
    def test_last_mention_wins(self):
        assert extract_mention_agent("@@genius then @@dev do it") == "dev"

    def test_no_mention(self):
        assert extract_mention_agent("just some text") is None


class TestExtractWorkflowCommand:
    def test_matches_first_line(self):
        assert extract_workflow_command("/workflow spec-driven-impl") == "spec-driven-impl"

    def test_ignores_later_lines(self):
        assert extract_workflow_command("hello\n/workflow default") is None


class TestExtractForceNextCommand:
    def test_bare(self):
        assert extract_force_next_command("/workflow force-next-step") == (
            "force-next-step",
            None,
        )

    def test_with_target_step(self):
        assert extract_force_next_command("/workflow force-next-step implement") == (
            "force-next-step",
            "implement",
        )

    def test_not_a_command(self):
        assert extract_force_next_command("some other command") is None


class TestExtractResumeCommand:
    def test_with_input(self):
        assert extract_resume_command("/workflow resume fix it") == "fix it"

    def test_bare_returns_empty_string(self):
        assert extract_resume_command("/workflow resume") == ""

    def test_not_a_command(self):
        assert extract_resume_command("/workflow default") is None


class TestExtractDebugFlag:
    def test_debug_true(self):
        assert extract_debug_flag("/workflow default DEBUG=true") is True

    def test_case_insensitive(self):
        assert extract_debug_flag("debug = True") is True

    def test_debug_false(self):
        assert extract_debug_flag("/workflow default DEBUG=false") is False


class TestBuildTriggerCommands:
    def test_bundles_all_fields(self):
        body = (
            "/workflow default DEBUG=true\n<!-- bot-agent-dev -->\n@@dev fix it"
        )
        tc = build_trigger_commands(body)
        assert isinstance(tc, TriggerCommands)
        assert tc.is_bot is True
        assert tc.workflow_cmd == "default"
        assert tc.debug is True
        assert tc.mention_agent == "dev"
        assert tc.force_next is None
        assert tc.resume is None

    def test_plain_body_all_neutral(self):
        tc = build_trigger_commands("nothing here")
        assert tc.is_bot is False
        assert tc.debug is False
        assert tc.mention_agent is None
        assert tc.workflow_cmd is None