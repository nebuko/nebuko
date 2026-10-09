"""Tests for the /nebuko <action> <resource> --flag value parser (issue #635)."""

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import BrandIdentity  # noqa: E402
from resolve_config.sub_domains.workflow_command.workflow_command import (  # noqa: E402
    ACTIONS,
    COMMAND_REGISTRY,
    FLAGS_BY_ACTION,
    RESOURCE_BY_ACTION,
    CommandGrammar,
    CommandParseResult,
    CommentIntent,
    WorkflowCommand,
    classify_comment,
    parse_command,
    parse_workflow_command,
)


class TestTables:
    def test_actions(self):
        assert ACTIONS == (
            "run",
            "resume",
            "force-step",
            "delete",
            "update",
            "approve",
            "reject",
        )

    def test_resource_by_action(self):
        assert RESOURCE_BY_ACTION == {
            "run": "workflow",
            "force-step": "step",
            "resume": None,
            "delete": "comments",
            "update": "circuit-breaker",
            "approve": "gate",
            "reject": "gate",
        }

    def test_flags_by_action(self):
        assert FLAGS_BY_ACTION == {
            "run": {"name", "step"},
            "force-step": {"name"},
            "resume": {"feedback"},
            "delete": {"issue", "from"},
            "update": {"value"},
            "approve": {"feedback"},
            "reject": {"feedback"},
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
        cmd = parse_workflow_command("/nebuko run workflow --name spec-driven-impl")
        assert cmd == WorkflowCommand(
            action="run", resource="workflow", params={"name": "spec-driven-impl"}
        )

    def test_run_with_step(self):
        cmd = parse_workflow_command(
            "/nebuko run workflow --name plan-orchestration --step scaffold"
        )
        assert cmd == WorkflowCommand(
            action="run",
            resource="workflow",
            params={"name": "plan-orchestration", "step": "scaffold"},
        )

    def test_run_step_still_requires_name(self):
        assert parse_workflow_command("/nebuko run workflow --step scaffold") is None

    def test_run_with_nested_step_path(self):
        # Dot-separated nested selector (issue #822): the grammar carries the
        # raw value through; StepPath.parse is the authoritative validator.
        cmd = parse_workflow_command(
            "/nebuko run workflow --name plan-orchestration --step sub_impl.step_3"
        )
        assert cmd == WorkflowCommand(
            action="run",
            resource="workflow",
            params={"name": "plan-orchestration", "step": "sub_impl.step_3"},
        )

    def test_run_with_deeply_nested_step_path(self):
        cmd = parse_workflow_command(
            "/nebuko run workflow --name plan-orchestration --step sub_impl.step_3.review"
        )
        assert cmd == WorkflowCommand(
            action="run",
            resource="workflow",
            params={"name": "plan-orchestration", "step": "sub_impl.step_3.review"},
        )

    def test_force_step_with_nested_step_path(self):
        cmd = parse_workflow_command(
            "/nebuko force-step step --name sub_impl.step_3"
        )
        assert cmd == WorkflowCommand(
            action="force-step", resource="step", params={"name": "sub_impl.step_3"}
        )

    def test_force_step(self):
        cmd = parse_workflow_command("/nebuko force-step step --name implement")
        assert cmd == WorkflowCommand(
            action="force-step", resource="step", params={"name": "implement"}
        )

    def test_resume_bare(self):
        cmd = parse_workflow_command("/nebuko resume")
        assert cmd == WorkflowCommand(action="resume", resource=None, params={})

    def test_resume_feedback_multiword(self):
        cmd = parse_workflow_command("/nebuko resume --feedback fix the bug")
        assert cmd == WorkflowCommand(
            action="resume", resource=None, params={"feedback": "fix the bug"}
        )

    def test_extra_lines_ignored(self):
        cmd = parse_workflow_command("/nebuko run workflow --name default\nmore text")
        assert cmd is not None and cmd.params == {"name": "default"}

    def test_leading_whitespace_allowed(self):
        cmd = parse_workflow_command("   /nebuko run workflow --name default")
        assert cmd is not None and cmd.action == "run"

    # /nebuko delete comments (issue #644)
    def test_delete_bare(self):
        cmd = parse_workflow_command("/nebuko delete comments")
        assert cmd == WorkflowCommand(action="delete", resource="comments", params={})

    def test_delete_issue(self):
        cmd = parse_workflow_command("/nebuko delete comments --issue 123")
        assert cmd == WorkflowCommand(
            action="delete", resource="comments", params={"issue": "123"}
        )

    def test_delete_accepts_from_flag(self):
        # `--from` scopes the deletion to that comment and every later one on
        # the target issue (restored in issue #867; supersedes the #674 removal).
        cmd = parse_workflow_command(
            "/nebuko delete comments --issue 123 --from 5811583860"
        )
        assert cmd == WorkflowCommand(
            action="delete",
            resource="comments",
            params={"issue": "123", "from": "5811583860"},
        )

    def test_delete_accepts_from_only(self):
        cmd = parse_workflow_command("/nebuko delete comments --from 5811583860")
        assert cmd == WorkflowCommand(
            action="delete", resource="comments", params={"from": "5811583860"}
        )

    # /nebuko clean comments (issue #679) — registry-local alias of delete;
    # it reuses delete's grammar so flags parse and validate identically.
    def test_clean_bare(self):
        cmd = parse_workflow_command("/nebuko clean comments")
        assert cmd == WorkflowCommand(action="clean", resource="comments", params={})

    def test_clean_issue(self):
        cmd = parse_workflow_command("/nebuko clean comments --issue 123")
        assert cmd == WorkflowCommand(
            action="clean", resource="comments", params={"issue": "123"}
        )

    def test_clean_from(self):
        # `clean` is an alias of `delete`, so it inherits `--from` (#867).
        cmd = parse_workflow_command("/nebuko clean comments --from 6095548211")
        assert cmd == WorkflowCommand(
            action="clean", resource="comments", params={"from": "6095548211"}
        )

    # /nebuko update circuit-breaker --value <n> (issue #839)
    def test_update_circuit_breaker(self):
        cmd = parse_workflow_command("/nebuko update circuit-breaker --value 35")
        assert cmd == WorkflowCommand(
            action="update",
            resource="circuit-breaker",
            params={"value": "35"},
        )

    def test_update_circuit_breaker_underscore_alias(self):
        # The issue's own spelling (`circuit_breaker`) is accepted as an alias
        # of the canonical hyphenated resource (issue #839).
        cmd = parse_workflow_command("/nebuko update circuit_breaker --value 35")
        assert cmd == WorkflowCommand(
            action="update",
            resource="circuit-breaker",
            params={"value": "35"},
        )


class TestParseMalformed:
    def test_unknown_action(self):
        assert parse_workflow_command("/nebuko deploy workflow --name x") is None

    def test_case_sensitive_action(self):
        assert parse_workflow_command("/nebuko Run workflow --name x") is None

    def test_old_positional_form(self):
        assert parse_workflow_command("/nebuko default") is None

    def test_old_resume_positional_form(self):
        assert parse_workflow_command("/nebuko resume fix it") is None

    def test_old_force_next_step_form(self):
        assert parse_workflow_command("/nebuko force-next-step implement") is None

    def test_missing_resource(self):
        assert parse_workflow_command("/nebuko run --name default") is None

    def test_wrong_resource(self):
        assert parse_workflow_command("/nebuko run step --name default") is None

    def test_resume_rejects_resource(self):
        assert parse_workflow_command("/nebuko resume step") is None

    def test_missing_required_name(self):
        assert parse_workflow_command("/nebuko run workflow") is None
        assert parse_workflow_command("/nebuko force-step step") is None

    def test_unknown_flag(self):
        assert parse_workflow_command("/nebuko run workflow --other default") is None

    def test_duplicate_flag(self):
        assert parse_workflow_command("/nebuko resume --feedback a --feedback b") is None

    def test_bare_flag_no_value(self):
        assert parse_workflow_command("/nebuko run workflow --name") is None
        assert parse_workflow_command("/nebuko resume --feedback") is None

    def test_resume_unknown_flag(self):
        assert parse_workflow_command("/nebuko resume --name x") is None

    def test_not_first_line(self):
        assert parse_workflow_command("hello\n/nebuko run workflow --name x") is None

    def test_empty_body(self):
        assert parse_workflow_command("") is None

    def test_workflow_only(self):
        assert parse_workflow_command("/nebuko") is None

    def test_prefix_not_command(self):
        assert parse_workflow_command("/nebukos default") is None

    # /nebuko delete comments (issue #644)
    def test_delete_non_numeric_issue(self):
        assert parse_workflow_command("/nebuko delete comments --issue abc") is None

    def test_delete_non_numeric_from(self):
        assert (
            parse_workflow_command("/nebuko delete comments --issue 123 --from abc") is None
        )

    def test_delete_wrong_resource(self):
        assert parse_workflow_command("/nebuko delete issues") is None
        assert (
            parse_workflow_command("/nebuko delete comments --issue 123 --issue 1") is None
        )

    def test_delete_unknown_flag(self):
        assert parse_workflow_command("/nebuko delete comments --all") is None

    def test_delete_bare_flag_no_value(self):
        assert parse_workflow_command("/nebuko delete comments --issue") is None
        assert parse_workflow_command("/nebuko delete comments --issue 123 --from") is None

    def test_delete_negative_issue_rejected(self):
        assert parse_workflow_command("/nebuko delete comments --issue -5") is None

    def test_delete_unicode_digit_rejected(self):
        # '²' passes str.isdigit() but int() rejects it; numeric flags must be
        # ASCII-only so the command is treated as absent (issue #644 review).
        assert parse_workflow_command("/nebuko delete comments --issue ²") is None
        assert parse_workflow_command("/nebuko delete comments --issue 123 --from ²") is None

    # Malformed /nebuko clean ... must not parse (issue #679 review): a
    # malformed invocation is not a recognized command and must not be able to
    # destructively delete comments.
    def test_clean_missing_resource_rejected(self):
        assert parse_workflow_command("/nebuko clean") is None

    def test_clean_wrong_resource_rejected(self):
        assert parse_workflow_command("/nebuko clean files") is None

    def test_clean_unknown_flag_rejected(self):
        assert parse_workflow_command("/nebuko clean comments --unknown x") is None

    def test_clean_non_numeric_issue_rejected(self):
        assert parse_workflow_command("/nebuko clean comments --issue abc") is None

    # /nebuko update circuit-breaker (issue #839): malformed forms must not parse.
    def test_update_missing_value_rejected(self):
        assert parse_workflow_command("/nebuko update circuit-breaker") is None

    def test_update_non_numeric_value_rejected(self):
        assert parse_workflow_command("/nebuko update circuit-breaker --value abc") is None

    def test_update_wrong_resource_rejected(self):
        assert parse_workflow_command("/nebuko update comments --value 35") is None

    def test_update_missing_resource_rejected(self):
        assert parse_workflow_command("/nebuko update --value 35") is None

    def test_update_unknown_flag_rejected(self):
        assert parse_workflow_command("/nebuko update circuit-breaker --limit 35") is None


class TestParseCommandTyped:
    """Typed parse surface that preserves the failure category (issue #804)."""

    def test_valid_command(self):
        result = parse_command("/nebuko run workflow --name default")
        assert result.status == "valid"
        assert result.detail == "ok"
        assert result.usage is None
        assert result.command == WorkflowCommand(
            action="run", resource="workflow", params={"name": "default"}
        )
        # The compatibility wrapper agrees with the typed result.
        assert parse_workflow_command("/nebuko run workflow --name default") == result.command

    def test_plain_prose_is_absent(self):
        result = parse_command("hello there, just a normal comment")
        assert result == CommandParseResult(
            status="absent", command=None, detail="none", usage=None
        )

    def test_unknown_slash_command(self):
        result = parse_command("/foo bar baz")
        assert result.status == "malformed"
        assert result.detail == "unknown_command"
        assert result.command is None
        assert result.usage and "/nebuko run workflow" in result.usage

    def test_unknown_slash_command_only_first_line(self):
        # A later-line slash does not count — only the first line is a command.
        assert parse_command("prose\n/foo").status == "absent"

    def test_bare_prefix_is_malformed_not_absent(self):
        result = parse_command("/nebuko")
        assert result.status == "malformed"
        assert result.detail == "unknown_action"
        assert result.usage

    def test_unknown_action(self):
        result = parse_command("/nebuko deploy workflow --name x")
        assert result.status == "malformed"
        assert result.detail == "unknown_action"
        assert result.usage

    def test_wrong_resource(self):
        result = parse_command("/nebuko run step --name default")
        assert result.status == "malformed"
        assert result.detail == "unknown_resource"
        assert "run workflow" in result.usage

    def test_unknown_flag(self):
        # The issue's own example: a bare "--default" instead of "--name default".
        result = parse_command("/nebuko run workflow --default")
        assert result.status == "malformed"
        assert result.detail == "unknown_flag"
        assert result.usage == "/nebuko run workflow --name <workflow> [--step <step[.step...]>]"

    def test_duplicate_flag(self):
        result = parse_command("/nebuko resume --feedback a --feedback b")
        assert result.status == "malformed"
        assert result.detail == "duplicate_flag"
        assert result.usage

    def test_bare_flag_value(self):
        result = parse_command("/nebuko run workflow --name")
        assert result.status == "malformed"
        assert result.detail == "bare_flag_value"
        assert result.usage

    def test_missing_required_flag(self):
        result = parse_command("/nebuko run workflow")
        assert result.status == "malformed"
        assert result.detail == "missing_required_flag"
        assert result.usage

    def test_invalid_flag_value(self):
        result = parse_command("/nebuko delete comments --issue abc")
        assert result.status == "malformed"
        assert result.detail == "invalid_flag_value"
        assert result.usage

    def test_clean_alias_keeps_its_own_usage(self):
        result = parse_command("/nebuko clean comments --unknown x")
        assert result.status == "malformed"
        assert result.detail == "unknown_flag"
        assert result.usage.startswith("/nebuko clean comments")

    def test_wrapper_returns_none_for_every_non_valid_result(self):
        bodies = [
            "plain prose",
            "",
            "/foo",
            "/nebuko",
            "/nebuko deploy workflow --name x",
            "/nebuko run workflow",
            "/nebuko run workflow --name",
            "/nebuko delete comments --issue abc",
        ]
        for body in bodies:
            assert parse_command(body).status != "valid"
            assert parse_workflow_command(body) is None


class TestCommandRegistry:
    def test_seeded_with_workflow_actions(self):
        # Every canonical /nebuko action is registered.
        for action in ACTIONS:
            assert action in COMMAND_REGISTRY

    def test_registers_clean(self):
        # `clean comments` is the bug trigger in issue #679 — it must be a
        # recognized command so it can never reach the agent-trigger path.
        assert "clean" in COMMAND_REGISTRY

    def test_registers_update(self):
        # `update circuit-breaker` is the deterministic, agent-free limit
        # command (issue #839); registration makes it a command-prefix
        # short-circuit so it never reaches the agent-trigger path.
        assert COMMAND_REGISTRY["update"] == "local"

    def test_gate_commands_are_engine_class(self):
        # approve/reject release a human gate inside the engine; they must
        # never be dispatched to an agent (issue #648).
        assert COMMAND_REGISTRY["approve"] == "engine"
        assert COMMAND_REGISTRY["reject"] == "engine"


class TestGateCommands:
    """`/nebuko approve gate` / `/nebuko reject gate` parsing (issue #648)."""

    def test_approve_gate_with_feedback(self):
        cmd = parse_workflow_command("/nebuko approve gate --feedback fix the X section")
        assert cmd == WorkflowCommand(
            action="approve", resource="gate", params={"feedback": "fix the X section"}
        )

    def test_reject_gate_without_feedback(self):
        cmd = parse_workflow_command("/nebuko reject gate")
        assert cmd == WorkflowCommand(action="reject", resource="gate", params={})

    def test_wrong_resource_is_malformed(self):
        assert parse_workflow_command("/nebuko approve workflow") is None

    def test_missing_resource_is_malformed(self):
        assert parse_workflow_command("/nebuko approve") is None

    def test_unknown_flag_is_malformed(self):
        assert parse_workflow_command("/nebuko approve gate --name x") is None

    def test_classify_approve_gate_is_command(self):
        intent = classify_comment("/nebuko approve gate")
        assert intent.kind == "command"
        assert intent.command == "approve"


class TestClassifyComment:
    def test_clean_is_command(self):
        intent = classify_comment("/nebuko clean comments")
        assert intent == CommentIntent(
            kind="command", command="clean", raw="/nebuko clean comments"
        )

    def test_delete_is_command(self):
        intent = classify_comment("/nebuko delete comments --issue 123")
        assert intent.kind == "command"
        assert intent.command == "delete"

    def test_update_is_command(self):
        intent = classify_comment("/nebuko update circuit-breaker --value 35")
        assert intent.kind == "command"
        assert intent.command == "update"

    def test_run_is_command(self):
        intent = classify_comment("/nebuko run workflow --name spec-driven-impl")
        assert intent.kind == "command"
        assert intent.command == "run"

    def test_mention_is_agent_trigger(self):
        intent = classify_comment("@@context please look at this")
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
        assert classify_comment("hello\n/nebuko clean comments").kind != "command"

    def test_bare_form_is_not_command(self):
        # Only the `/nebuko`-prefixed form is recognized — the real trigger
        # payload is `/nebuko clean comments` (issue #590). A bare
        # `clean comments` line must not be classified as a command (issue #679).
        assert classify_comment("clean comments").kind != "command"

    def test_malformed_clean_is_a_command_attempt_not_a_command(self):
        # A malformed `clean` invocation is not a recognized command, so it can
        # never destructively delete comments (issue #679); it *is* a command
        # attempt, so the resolver answers with the usage hint and never boots
        # an agent (issue #867).
        for body in (
            "/nebuko clean",
            "/nebuko clean files",
            "/nebuko clean comments --unknown x",
        ):
            intent = classify_comment(body)
            assert intent.kind == "command_attempt", body
            assert intent.command == "clean", body
            assert intent.usage and intent.usage.startswith(
                "/nebuko clean comments"
            ), body
            assert intent.detail, body

    def test_frozen(self):
        intent = CommentIntent(
            kind="command", command="clean", raw="/nebuko clean comments"
        )
        with pytest.raises(FrozenInstanceError):
            intent.kind = "none"  # type: ignore[misc]


class TestCommandAttempts:
    """A malformed /nebuko invocation classifies as a command attempt (#867)."""

    MALFORMED_NEBUKO = [
        "/nebuko",  # bare prefix, no action
        "/nebuko clean",  # missing resource
        "/nebuko clean files",  # wrong resource
        "/nebuko clean comments --unknown x",  # unknown flag
        "/nebuko clean comments --issue abc",  # non-numeric flag
        "/nebuko delete comments --from abc",  # non-numeric --from
        "/nebuko run workflow",  # missing required flag
        "/nebuko run workflow --name",  # bare flag value
        "/nebuko resume --feedback a --feedback b",  # duplicate flag
        "/nebuko frobnicate x",  # unknown action
        "/nebuko approve --bogus",  # missing resource
    ]

    def test_every_malformed_nebuko_body_is_a_command_attempt(self):
        for body in self.MALFORMED_NEBUKO:
            assert parse_command(body).status == "malformed", body
            assert classify_comment(body).kind == "command_attempt", body

    def test_attempt_carries_usage_detail_and_verb(self):
        intent = classify_comment("/nebuko clean comments --bogus x")
        assert intent.kind == "command_attempt"
        assert intent.command == "clean"
        assert intent.detail == "unknown_flag"
        assert intent.usage == (
            "/nebuko clean comments [--issue <number>] [--from <comment-id>]"
        )

    def test_unknown_action_carries_no_verb_but_aggregate_usage(self):
        intent = classify_comment("/nebuko frobnicate x")
        assert intent.kind == "command_attempt"
        assert intent.command is None
        assert "unrecognized command" in (intent.usage or "")
        assert intent.detail == "unknown_action"

    def test_foreign_slash_is_not_a_command_attempt(self):
        # A leading "/" that is not a /nebuko invocation is not our attempt; it
        # keeps the ADR-0004 fallback path.
        assert classify_comment("/foo bar").kind == "none"

    def test_prose_is_not_a_command_attempt(self):
        assert classify_comment("just some prose").kind == "none"

    def test_mention_with_malformed_command_is_a_command_attempt(self):
        # The attempted command outranks an embedded mention: the grammar alone
        # decides a /nebuko body (issue #867).
        intent = classify_comment("/nebuko clean comments --bogus x @@dev please")
        assert intent.kind == "command_attempt"


class TestCommandGrammar:
    """The command prefix is owned by CommandGrammar, sourced from brand (issue #749)."""

    def test_brand_command_name_defaults_to_nebuko(self):
        assert BrandIdentity().command_name == "nebuko"

    def test_frozen(self):
        grammar = CommandGrammar(primary="nebuko")
        with pytest.raises(FrozenInstanceError):
            grammar.primary = "workflow"  # type: ignore[misc]

    def test_pattern_matches_brand_prefix(self):
        grammar = CommandGrammar(primary=BrandIdentity().command_name)
        match = grammar.pattern().match("/nebuko run workflow --name spec-driven-impl")
        assert match is not None
        assert match.group(1) == "run workflow --name spec-driven-impl"

    def test_pattern_rejects_near_prefix(self):
        pattern = CommandGrammar(primary="nebuko").pattern()
        assert pattern.match("/nebukox run workflow") is None

    def test_pattern_rejects_legacy_prefix(self):
        pattern = CommandGrammar(primary="nebuko").pattern()
        assert pattern.match("/workflow run workflow") is None


class TestNebukoPrefix:
    """The parser recognises /nebuko and drops the legacy /workflow prefix (issue #749)."""

    def test_nebuko_run_parses(self):
        cmd = parse_workflow_command("/nebuko run workflow --name spec-driven-impl")
        assert cmd == WorkflowCommand(
            action="run", resource="workflow", params={"name": "spec-driven-impl"}
        )

    def test_nebuko_bare_resume_parses(self):
        cmd = parse_workflow_command("/nebuko resume")
        assert cmd == WorkflowCommand(action="resume", resource=None, params={})

    def test_legacy_workflow_prefix_is_no_longer_a_command(self):
        cmd = parse_workflow_command("/workflow run workflow --name spec-driven-impl")
        assert cmd is None

    def test_near_prefix_is_not_a_command(self):
        assert parse_workflow_command("/nebukox run workflow --name x") is None

    def test_classify_nebuko_clean_is_command(self):
        intent = classify_comment("/nebuko clean comments")
        assert intent.kind == "command"
        assert intent.command == "clean"

    def test_classify_legacy_workflow_clean_is_not_command(self):
        assert classify_comment("/workflow clean comments").kind == "none"
