"""Tests for the /workflow <action> <resource> --flag value parser (issue #635)."""

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.sub_domains.workflow_command.workflow_command import (  # noqa: E402
    ACTIONS,
    COMMAND_REGISTRY,
    FLAGS_BY_ACTION,
    RESOURCE_BY_ACTION,
    CommentIntent,
    WorkflowCommand,
    classify_comment,
    parse_workflow_command,
)


class TestTables:
    def test_actions(self):
        assert ACTIONS == ("run", "resume", "force-step", "delete")

    def test_resource_by_action(self):
        assert RESOURCE_BY_ACTION == {
            "run": "workflow",
            "force-step": "step",
            "resume": None,
            "delete": "comments",
        }

    def test_flags_by_action(self):
        assert FLAGS_BY_ACTION == {
            "run": {"name"},
            "force-step": {"name"},
            "resume": {"feedback"},
            "delete": {"issue"},
        }


class TestWorkflowCommand:
    def test_frozen(self):
        cmd = WorkflowCommand(action="run", resource="workflow", params={"name": "default"})
        try:
            cmd.action = "resume"  # type: ignore[misc]
            assert False, "frozen dataclass should reject assignment"
        except Exception:
            pass

    def test_fields(self):
        cmd = WorkflowCommand(action="resume", resource=None, params={})
        assert cmd.action == "resume"
        assert cmd.resource is None
        assert cmd.params == {}


class TestParseValid:
    def test_run(self):
        cmd = parse_workflow_command("/workflow run workflow --name spec-driven-impl")
        assert cmd == WorkflowCommand(
            action="run", resource="workflow", params={"name": "spec-driven-impl"}
        )

    def test_force_step(self):
        cmd = parse_workflow_command("/workflow force-step step --name implement")
        assert cmd == WorkflowCommand(
            action="force-step", resource="step", params={"name": "implement"}
        )

    def test_resume_bare(self):
        cmd = parse_workflow_command("/workflow resume")
        assert cmd == WorkflowCommand(action="resume", resource=None, params={})

    def test_resume_feedback_multiword(self):
        cmd = parse_workflow_command("/workflow resume --feedback fix the bug")
        assert cmd == WorkflowCommand(
            action="resume", resource=None, params={"feedback": "fix the bug"}
        )

    def test_extra_lines_ignored(self):
        cmd = parse_workflow_command("/workflow run workflow --name default\nmore text")
        assert cmd is not None and cmd.params == {"name": "default"}

    def test_leading_whitespace_allowed(self):
        cmd = parse_workflow_command("   /workflow run workflow --name default")
        assert cmd is not None and cmd.action == "run"

    # /workflow delete comments (issue #644)
    def test_delete_bare(self):
        cmd = parse_workflow_command("/workflow delete comments")
        assert cmd == WorkflowCommand(action="delete", resource="comments", params={})

    def test_delete_issue(self):
        cmd = parse_workflow_command("/workflow delete comments --issue 123")
        assert cmd == WorkflowCommand(
            action="delete", resource="comments", params={"issue": "123"}
        )

    def test_delete_rejects_from_flag(self):
        # `--from` support was removed in issue #674: delete the whole issue.
        assert (
            parse_workflow_command("/workflow delete comments --issue 123 --from 5811583860")
            is None
        )

    def test_delete_from_only_rejected(self):
        assert (
            parse_workflow_command("/workflow delete comments --from 5811583860") is None
        )


class TestParseMalformed:
    def test_unknown_action(self):
        assert parse_workflow_command("/workflow deploy workflow --name x") is None

    def test_case_sensitive_action(self):
        assert parse_workflow_command("/workflow Run workflow --name x") is None

    def test_old_positional_form(self):
        assert parse_workflow_command("/workflow default") is None

    def test_old_resume_positional_form(self):
        assert parse_workflow_command("/workflow resume fix it") is None

    def test_old_force_next_step_form(self):
        assert parse_workflow_command("/workflow force-next-step implement") is None

    def test_missing_resource(self):
        assert parse_workflow_command("/workflow run --name default") is None

    def test_wrong_resource(self):
        assert parse_workflow_command("/workflow run step --name default") is None

    def test_resume_rejects_resource(self):
        assert parse_workflow_command("/workflow resume step") is None

    def test_missing_required_name(self):
        assert parse_workflow_command("/workflow run workflow") is None
        assert parse_workflow_command("/workflow force-step step") is None

    def test_unknown_flag(self):
        assert parse_workflow_command("/workflow run workflow --other default") is None

    def test_duplicate_flag(self):
        assert parse_workflow_command("/workflow resume --feedback a --feedback b") is None

    def test_bare_flag_no_value(self):
        assert parse_workflow_command("/workflow run workflow --name") is None
        assert parse_workflow_command("/workflow resume --feedback") is None

    def test_resume_unknown_flag(self):
        assert parse_workflow_command("/workflow resume --name x") is None

    def test_not_first_line(self):
        assert parse_workflow_command("hello\n/workflow run workflow --name x") is None

    def test_empty_body(self):
        assert parse_workflow_command("") is None

    def test_workflow_only(self):
        assert parse_workflow_command("/workflow") is None

    def test_prefix_not_command(self):
        assert parse_workflow_command("/workflows default") is None

    # /workflow delete comments (issue #644)
    def test_delete_non_numeric_issue(self):
        assert parse_workflow_command("/workflow delete comments --issue abc") is None

    def test_delete_non_numeric_from(self):
        assert (
            parse_workflow_command("/workflow delete comments --issue 123 --from abc") is None
        )

    def test_delete_wrong_resource(self):
        assert parse_workflow_command("/workflow delete issues") is None
        assert (
            parse_workflow_command("/workflow delete comments --issue 123 --issue 1") is None
        )

    def test_delete_unknown_flag(self):
        assert parse_workflow_command("/workflow delete comments --all") is None

    def test_delete_bare_flag_no_value(self):
        assert parse_workflow_command("/workflow delete comments --issue") is None
        assert parse_workflow_command("/workflow delete comments --issue 123 --from") is None

    def test_delete_negative_issue_rejected(self):
        assert parse_workflow_command("/workflow delete comments --issue -5") is None

    def test_delete_unicode_digit_rejected(self):
        # '²' passes str.isdigit() but int() rejects it; numeric flags must be
        # ASCII-only so the command is treated as absent (issue #644 review).
        assert parse_workflow_command("/workflow delete comments --issue ²") is None
        assert parse_workflow_command("/workflow delete comments --issue 123 --from ²") is None


class TestCommandRegistry:
    def test_seeded_with_workflow_actions(self):
        # Every canonical /workflow action is registered.
        for action in ACTIONS:
            assert action in COMMAND_REGISTRY

    def test_registers_clean(self):
        # `clean comments` is the bug trigger in issue #679 — it must be a
        # recognized command so it can never reach the agent-trigger path.
        assert "clean" in COMMAND_REGISTRY


class TestClassifyComment:
    def test_clean_is_command(self):
        intent = classify_comment("/workflow clean comments")
        assert intent == CommentIntent(
            kind="command", command="clean", raw="/workflow clean comments"
        )

    def test_delete_is_command(self):
        intent = classify_comment("/workflow delete comments --issue 123")
        assert intent.kind == "command"
        assert intent.command == "delete"

    def test_run_is_command(self):
        intent = classify_comment("/workflow run workflow --name spec-driven-impl")
        assert intent.kind == "command"
        assert intent.command == "run"

    def test_mention_is_agent_trigger(self):
        intent = classify_comment("@@ctx please look at this")
        assert intent.kind == "agent_trigger"
        assert intent.command is None

    def test_machine_trigger_is_agent_trigger(self):
        intent = classify_comment("<!-- bot-subtask-complete: 1 -->")
        assert intent.kind == "agent_trigger"

    def test_free_form_is_none(self):
        assert classify_comment("this is just a normal comment").kind == "none"

    def test_prose_mentioning_clean_is_not_command(self):
        # A genuine free-form comment that merely contains the words must not
        # be misclassified as a command (issue #679 over-matching guard).
        assert classify_comment("we should clean comments later").kind != "command"

    def test_clean_not_on_first_line_is_not_command(self):
        assert classify_comment("hello\n/workflow clean comments").kind != "command"

    def test_bare_form_is_not_command(self):
        # Only the `/workflow`-prefixed form is recognized — the real trigger
        # payload is `/workflow clean comments` (issue #590). A bare
        # `clean comments` line must not be classified as a command (issue #679).
        assert classify_comment("clean comments").kind != "command"

    def test_frozen(self):
        intent = CommentIntent(
            kind="command", command="clean", raw="/workflow clean comments"
        )
        with pytest.raises(FrozenInstanceError):
            intent.kind = "none"  # type: ignore[misc]
