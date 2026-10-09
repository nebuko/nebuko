"""Tests for parse_trigger feature (pure string extraction)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.features.parse_trigger.parse_trigger import (  # noqa: E402
    build_trigger_commands,
    extract_debug_flag,
    extract_machine_trigger,
    extract_mention_agent,
    is_bot_comment,
    is_machine_comment,
    is_plain_mention_trigger,
    normalize_resume_input,
    BARE_RESUME_INPUT,
)
from resolve_config.sub_domains.trigger_commands.trigger_commands import (  # noqa: E402
    TriggerCommands,
)
from resolve_config.sub_domains.workflow_command.workflow_command import (  # noqa: E402
    parse_workflow_command,
    WorkflowCommand,
)


class TestIsBotComment:
    def test_bot_marker_in_first_lines(self):
        assert is_bot_comment("<!-- bot-agent-dev -->\nbody") is True

    def test_universal_marker_in_first_line(self):
        assert is_bot_comment("<!-- bot-workflow-terminal -->\nbody") is True

    def test_universal_marker_on_line_four(self):
        assert is_bot_comment("a\nb\nc\n<!-- bot-workflow-terminal -->") is True

    def test_universal_marker_beyond_five_lines(self):
        assert is_bot_comment("a\nb\nc\nd\ne\n<!-- bot-workflow-terminal -->") is False

    def test_bot_marker_beyond_five_lines(self):
        assert is_bot_comment("a\nb\nc\nd\ne\n<!-- bot-agent-dev -->") is False


class TestIsPlainMentionTrigger:
    """Test is_plain_mention_trigger function."""

    def test_bootstrap_architect_with_universal_marker(self):
        body = "<!-- bot-workflow-terminal -->\n@@architect"
        assert is_plain_mention_trigger(body) is True

    def test_subtask_completion_notification(self):
        body = "@@architect <!-- bot-workflow-terminal --> <!-- subtask-complete: 2 -->"
        assert is_plain_mention_trigger(body) is True

    def test_bare_human_mention_is_plain(self):
        # Human comments route via human_mention, but shape detection still
        # matches a bare mention.
        assert is_plain_mention_trigger("@@context") is True

    def test_prose_after_mention_not_plain(self):
        body = "<!-- bot-agent-dev -->\n@@architect please analyze this"
        assert is_plain_mention_trigger(body) is False

    def test_agent_answer_with_mention_in_text_not_plain(self):
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- bot-agent-architect -->\n"
            "*Architect answer*\n\nHanding off to @@dev for implementation."
        )
        assert is_plain_mention_trigger(body) is False

    def test_no_mention_not_plain(self):
        assert is_plain_mention_trigger("just some text") is False

    def test_empty_body_not_plain(self):
        assert is_plain_mention_trigger("") is False

    def test_multi_line_markers_only(self):
        body = "\n".join(
            [
                "<!-- bot-workflow-terminal -->",
                "<!-- bot-agent-context -->",
                "@@architect",
            ]
        )
        assert is_plain_mention_trigger(body) is True

    def test_plain_body(self):
        assert is_bot_comment("hello world") is False


class TestExtractMentionAgent:
    def test_last_mention_wins(self):
        assert extract_mention_agent("@@architect then @@dev do it") == "dev"

    def test_no_mention(self):
        assert extract_mention_agent("just some text") is None

    def test_mention_po(self):
        assert extract_mention_agent("@@po please write the spec") == "po"

    def test_removed_agent_mentions_no_longer_route(self):
        # debug/issue/issue-reviewer were retired in issue #828.
        assert extract_mention_agent("@@debug investigate this") is None
        assert extract_mention_agent("@@issue open a ticket") is None
        assert extract_mention_agent("@@issue-reviewer take a look") is None

    def test_legacy_agent_mentions_still_route(self):
        # Deprecated names resolve to their renamed successors (issue #828).
        assert extract_mention_agent("@@genius plan this") == "architect"
        assert extract_mention_agent("@@ctx gather context") == "context"

    def test_all_configured_agents_mentionable(self):
        from config import load_agent_names

        for agent in sorted(load_agent_names()):
            assert extract_mention_agent(f"@@{agent} go") == agent

    def test_unknown_mention_ignored(self):
        assert extract_mention_agent("@@nobody please help") is None

    def test_partial_name_not_matched(self):
        # `@@polaris` starts with `po` but must not resolve to the `po` agent.
        assert extract_mention_agent("@@polaris please help") is None

    def test_bot_body_never_yields_mention(self):
        # Bots cannot @@mention — stray tokens in bot prose must not re-route
        # execution (issue #478).
        assert (
            extract_mention_agent("<!-- bot-agent-architect -->\nask @@context please")
            is None
        )

    def test_bot_body_warning_logs_ignored_mentions(self, capsys):
        extract_mention_agent(
            "<!-- bot-agent-dev -->\nsee @@architect and @@context above"
        )
        err = capsys.readouterr().err
        assert "@@architect" in err and "@@context" in err


class TestExtractMachineTrigger:
    def test_subtask_complete_maps_to_architect_with_index(self):
        assert extract_machine_trigger(
            "<!-- bot-subtask-complete: 3 -->"
        ) == ("architect", "3")

    def test_generic_trigger(self):
        assert extract_machine_trigger(
            "<!-- bot-trigger: review -->"
        ) == ("review", None)

    def test_no_token(self):
        assert extract_machine_trigger("plain human comment @@dev") is None

    def test_subtask_complete_beats_generic(self):
        body = "<!-- bot-subtask-complete: 1 -->\n<!-- bot-trigger: dev -->"
        assert extract_machine_trigger(body) == ("architect", "1")


class TestNormalizeResumeInput:
    def test_nonempty_text_preserved(self):
        assert normalize_resume_input("approved") == "approved"

    def test_whitespace_only_becomes_sentinel(self):
        assert normalize_resume_input("   ") == BARE_RESUME_INPUT

    def test_empty_string_becomes_sentinel(self):
        assert normalize_resume_input("") == BARE_RESUME_INPUT

    def test_none_returns_empty(self):
        assert normalize_resume_input(None) == ""

    def test_surrounding_whitespace_stripped(self):
        assert normalize_resume_input("  lgtm  ") == "lgtm"


class TestExtractDebugFlag:
    def test_debug_true(self):
        assert extract_debug_flag("/nebuko default DEBUG=true") is True

    def test_case_insensitive(self):
        assert extract_debug_flag("debug = True") is True

    def test_debug_false(self):
        assert extract_debug_flag("/nebuko default DEBUG=false") is False


class TestBuildTriggerCommands:
    def test_bundles_all_fields(self):
        body = (
            "/nebuko run workflow --name default\n"
            "<!-- bot-agent-dev -->\n@@dev fix it\nDEBUG=true"
        )
        tc = build_trigger_commands(body)
        assert isinstance(tc, TriggerCommands)
        assert tc.is_bot is True
        assert tc.workflow_command is not None
        assert tc.workflow_command.action == "run"
        assert tc.workflow_command.params == {"name": "default"}
        assert tc.debug is True
        # Bot bodies never yield a mention (issue #478).
        assert tc.mention_agent is None

    def test_plain_body_all_neutral(self):
        tc = build_trigger_commands("nothing here")
        assert tc.is_bot is False
        assert tc.debug is False
        assert tc.mention_agent is None
        assert tc.workflow_command is None


class TestIsMachineComment:
    """Whole-body machine-token detection for the gate path (issue #648)."""

    def test_plain_human_body(self):
        assert is_machine_comment("/nebuko approve gate") is False

    def test_universal_bot_marker(self):
        assert is_machine_comment("<!-- bot-workflow-terminal -->\nhello") is True

    def test_agent_marker_anywhere_in_body(self):
        # is_bot_comment only checks the first lines; is_machine_comment must
        # catch a token echoed deep in the body.
        body = ("prose\n" * 20) + "<!-- bot-agent-dev -->"
        assert is_machine_comment(body) is True

    def test_agent_run_config_token(self):
        assert is_machine_comment('{"AGENT_RUN_CONFIG": {}}') is True

    def test_bot_trigger_token(self):
        assert is_machine_comment("<!-- bot-trigger: dev -->") is True

    def test_subtask_and_summarizer_tokens(self):
        assert is_machine_comment("<!-- bot-subtask-complete: 1 -->") is True
        assert is_machine_comment("<!-- bot-summarizer-trigger -->") is True

