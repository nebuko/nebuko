"""Spec-scenario tests for the agent-config linter (issue #774).

The workflow linter (``nebuko.engine.lint``) covers graph-level defects; these
scenarios pin the sibling ``ConfigLinter`` contract (rules C1–C8) for the layered
agent-config: every fixture is a complete providers → llms → agents mapping so
the other rules stay quiet unless they are the subject of the scenario.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from nebuko.engine.config_lint import ConfigLinter, KNOWN_TOOLS, RULE_SEVERITY
from nebuko.engine.lint import LintIssue

FIXTURES = Path(__file__).parent / "config_lint_fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Rules and severities fixed by the approved plan (issue #774) plus C8 from
# issue #476: C6 and C8 are the warnings that ``strict`` promotes; every other
# rule is an error.
RULE_SEVERITY_EXPECTED = {
    "C1-schema": "error",
    "C2-dangling-ref": "error",
    "C3-unknown-tool": "error",
    "C4-prompt-file": "error",
    "C5-skill-file": "error",
    "C6-unreferenced-entry": "warning",
    "C7-mcp-servers": "error",
    "C8-missing-role": "warning",
    "C9-deprecated-tool": "warning",
}

FILE_RULES = ("C4-prompt-file", "C5-skill-file")

KNOWN_TOOLS_EXPECTED = frozenset(
    {
        "terminal",
        "terminal_batch",
        "file_editor",
        "task_tracker",
        "think",
        "finish",
        "finalize",
        "report_scaffold_branch",
        "complexity_review",
    }
)


def _lint(fixture: str, *, strict: bool = False) -> list[LintIssue]:
    return ConfigLinter(strict=strict).lint(FIXTURES / fixture)


def _by_rule(issues: list[LintIssue], rule: str) -> list[LintIssue]:
    return [issue for issue in issues if issue.rule == rule]


class TestLintIssueContract:
    def test_field_order_matches_the_frozen_contract(self):
        assert [f.name for f in dataclasses.fields(LintIssue)] == [
            "rule",
            "severity",
            "message",
            "step",
            "location",
            "hint",
        ]

    def test_config_issues_leave_step_empty(self):
        issue = _lint("c3_unknown_tool.yaml")[0]
        assert issue.step == ""
        assert issue.location == "agents.a.tools"


class TestConfigLinterContract:
    def test_rule_severities_match_the_plan(self):
        assert RULE_SEVERITY == RULE_SEVERITY_EXPECTED

    def test_known_tools_match_the_runtime_whitelist(self):
        assert KNOWN_TOOLS == KNOWN_TOOLS_EXPECTED

    def test_exposes_the_plan_rule_methods(self):
        for name in (
            "_check_refs",
            "_check_schema",
            "_check_tools",
            "_check_roles",
            "_check_mcp_servers",
            "_check_prompt_files",
            "_check_skill_files",
            "_check_unreferenced",
        ):
            assert callable(getattr(ConfigLinter, name))

    def test_defaults_to_non_strict_without_a_root(self):
        linter = ConfigLinter()
        assert linter._strict is False
        assert linter._root is None


class TestRuleScenarios:
    def test_c1_collects_every_schema_error_not_just_the_first(self):
        # ``c1_schema.yaml`` declares no ``role``, so C8 is reported alongside —
        # the scenario is about C1 collecting every schema error, not the first.
        issues = _by_rule(_lint("c1_schema.yaml"), "C1-schema")
        locations = {issue.location for issue in issues}
        assert {"agents.a.temperature", "agents.a.prompt_file"} <= locations

    def test_c2_reports_every_dangling_ref(self):
        refs = _by_rule(_lint("c2_dangling_ref.yaml"), "C2-dangling-ref")
        assert {issue.location for issue in refs} == {
            "agents.a.model",
            "agents.b.model",
        }
        assert all(issue.severity == "error" for issue in refs)

    def test_c3_flags_an_unknown_tool(self):
        issues = _by_rule(_lint("c3_unknown_tool.yaml"), "C3-unknown-tool")
        assert len(issues) == 1
        assert issues[0].location == "agents.a.tools"
        assert "warp_drive" in issues[0].message

    def test_c4_flags_a_missing_prompt_file(self):
        issues = _by_rule(_lint("c4_missing_prompt.yaml"), "C4-prompt-file")
        assert [issue.location for issue in issues] == ["agents.a.prompt_file"]

    def test_c4_accepts_a_url_prompt_file(self):
        assert _by_rule(_lint("url_prompt.yaml"), "C4-prompt-file") == []

    def test_c5_flags_a_missing_skill_file(self):
        issues = _by_rule(_lint("c5_missing_skill.yaml"), "C5-skill-file")
        assert [issue.location for issue in issues] == ["agents.a.skills"]

    def test_c5_validates_names_inside_mode_entries(self):
        """Issue #955: `- discover: [...]` entries are walked, not skipped."""
        issues = _by_rule(_lint("c5_missing_skill_entry.yaml"), "C5-skill-file")

        assert [issue.location for issue in issues] == ["agents.a.skills"]
        assert "ghost-skill" in issues[0].message

    def test_c5_accepts_a_resolvable_name_in_a_mode_entry(self):
        assert _by_rule(_lint("clean_mode_skills.yaml"), "C5-skill-file") == []

    def test_c8_warns_on_a_missing_or_blank_role(self):
        issues = _by_rule(_lint("c8_missing_role.yaml"), "C8-missing-role")
        assert [issue.location for issue in issues] == [
            "agents.a.role",
            "agents.b.role",
        ]
        assert all(issue.severity == "warning" for issue in issues)

    def test_c6_warns_on_dead_llm_and_provider_entries(self):
        issues = _by_rule(_lint("c6_unreferenced.yaml"), "C6-unreferenced-entry")
        assert {issue.location for issue in issues} == {
            "llms.l_unused",
            "providers.p_unused",
        }
        assert all(issue.severity == "warning" for issue in issues)

    def test_c7_flags_every_malformed_mcp_server(self):
        issues = _by_rule(_lint("c7_mcp_servers.yaml"), "C7-mcp-servers")
        locations = {issue.location for issue in issues}
        assert locations == {
            "agents.a.mcp_servers[0].name",
            "agents.a.mcp_servers[0].command",
            "agents.a.mcp_servers[0].args",
            "agents.a.mcp_servers[0].env.BRAVE_API_KEY",
            "agents.a.mcp_servers[0].env.2BAD",
            "agents.a.mcp_servers[2].name",
            "agents.a.mcp_servers[2].transport",
        }
        assert all(issue.severity == "error" for issue in issues)
        assert _by_rule(_lint("clean.yaml"), "C7-mcp-servers") == []

    def test_c7_flags_root_catalog_and_ref_entries(self):
        issues = _by_rule(_lint("c7_mcp_catalog.yaml"), "C7-mcp-servers")
        locations = {issue.location for issue in issues}
        assert locations == {
            "mcp_servers.brave-search.name",
            "mcp_servers.brave-search.command",
            "mcp_servers.brave-search.args",
            "mcp_servers.brave-search.env.BRAVE_API_KEY",
            "mcp_servers.malformed",
        }
        assert all(issue.severity == "error" for issue in issues)

    def test_c2_reports_a_dangling_mcp_catalog_ref(self):
        issues = _by_rule(_lint("c7_mcp_catalog.yaml"), "C2-dangling-ref")
        assert [issue.location for issue in issues] == ["agents.a.mcp_servers[2]"]

    def test_c6_warns_on_an_unreferenced_mcp_catalog_entry(self):
        issues = _by_rule(_lint("c6_mcp_unreferenced.yaml"), "C6-unreferenced-entry")
        assert {issue.location for issue in issues} == {"mcp_servers.dead-server"}
        assert all(issue.severity == "warning" for issue in issues)

    def test_a_clean_config_reports_no_issues(self):
        assert _lint("clean.yaml") == []

    def test_a_clean_catalog_config_reports_no_issues(self):
        assert _lint("clean_mcp_catalog.yaml") == []

    def test_multi_error_covers_every_rule(self):
        rules = {issue.rule for issue in _lint("multi_error.yaml")}
        assert set(RULE_SEVERITY_EXPECTED) <= rules

    def test_lint_all_concatenates_per_file_issues(self):
        issues = ConfigLinter().lint_all(
            [FIXTURES / "clean.yaml", FIXTURES / "c3_unknown_tool.yaml"]
        )
        assert any(issue.rule == "C3-unknown-tool" for issue in issues)

    def test_strict_promotes_warnings_to_errors(self):
        issues = _lint("c6_unreferenced.yaml", strict=True)
        assert all(issue.severity == "error" for issue in issues)

    def test_every_reported_severity_matches_the_plan(self):
        for fixture in (
            "c1_schema.yaml",
            "c2_dangling_ref.yaml",
            "c3_unknown_tool.yaml",
            "c4_missing_prompt.yaml",
            "c5_missing_skill.yaml",
            "c6_unreferenced.yaml",
            "multi_error.yaml",
        ):
            for issue in _lint(fixture):
                assert issue.severity == RULE_SEVERITY_EXPECTED[issue.rule]


class TestShippedConfigs:
    """The configs Nebuko ships must resolve through the layered file lookup."""

    def test_shipped_configs_have_no_prompt_or_skill_issues(self):
        shipped = sorted((REPO_ROOT / "defaults" / "agents").glob("agent-config*.yaml"))
        assert shipped
        for path in shipped:
            issues = ConfigLinter().lint(path)
            assert [issue for issue in issues if issue.rule in FILE_RULES] == []
