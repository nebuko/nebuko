"""Tests for the /workflow <action> <resource> --flag value parser (issue #635)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.sub_domains.workflow_command.workflow_command import (  # noqa: E402
    ACTIONS,
    FLAGS_BY_ACTION,
    RESOURCE_BY_ACTION,
    WorkflowCommand,
    parse_workflow_command,
)


class TestTables:
    def test_actions(self):
        assert ACTIONS == ("run", "resume", "force-step")

    def test_resource_by_action(self):
        assert RESOURCE_BY_ACTION == {"run": "workflow", "force-step": "step", "resume": None}

    def test_flags_by_action(self):
        assert FLAGS_BY_ACTION == {
            "run": {"name"},
            "force-step": {"name"},
            "resume": {"feedback"},
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