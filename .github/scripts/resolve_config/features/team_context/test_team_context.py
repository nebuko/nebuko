"""Tests for the team_context feature (issue #476)."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.features.team_context.team_context import (  # noqa: E402
    SHARED_CONTEXT_DEFAULT_PATH,
    build_caller_line,
    build_shared_context,
    build_team_env,
    build_team_section,
    default_config_path,
    load_agent_skills,
    load_team_data,
)

ROLES = {
    "genius": "Principal engineer",
    "dev": "Execution developer",
    "ctx": "Context collector",
}


class TestBuildTeamSection:
    def test_empty_roles_returns_empty(self):
        assert build_team_section({}, "dev") == ""

    def test_lists_self_and_teammates(self):
        section = build_team_section(ROLES, "dev")
        assert "# Your Team" in section
        assert "You are @@@dev — Execution developer" in section
        assert "- @@@genius: Principal engineer" in section
        assert "- @@@ctx: Context collector" in section
        assert "- @@@dev:" not in section  # self is not a teammate

    def test_delegation_rules_present(self):
        section = build_team_section(ROLES, "dev")
        assert "@@<name>" in section
        assert "call them back with your answer" in section

    def test_missing_role_for_agent(self):
        section = build_team_section({"dev": ""}, "dev")
        assert "You are @@@dev" in section
        assert "Teammates:" not in section


class TestBuildCallerLine:
    def test_no_caller(self):
        assert build_caller_line("", "dev") == ""

    def test_self_call_omitted(self):
        assert build_caller_line("dev", "dev") == ""

    def test_caller_line_rendered(self):
        line = build_caller_line("genius", "dev")
        assert "You were called by @@@genius" in line
        assert "call @@@genius back" in line


class TestBuildSharedContext:
    def test_includes_team_tools_caller_and_exit_rules(self):
        text = build_shared_context(
            ROLES, "dev", caller="genius", tools=["terminal", "finish"]
        )
        assert "# Your Team" in text
        assert "You may only use these tools: terminal, finish" in text
        assert "You were called by @@@genius" in text
        assert "Strict Output & Exit Rules" in text
        assert "AGENT_RUN_CONFIG" in text

    def test_no_roles_returns_empty_even_with_tools(self):
        # Roster is the anchor: without roles, no shared section is injected.
        assert build_shared_context({}, "dev", caller="genius", tools=["finish"]) == ""

    def test_no_roles_no_tools_returns_empty(self):
        assert build_shared_context({}, "dev", caller="", tools=[]) == ""


class TestLoadTeamData:
    def test_missing_config_returns_empty(self):
        roles, tools = load_team_data("/nonexistent/agent-config.yaml")
        assert roles == {} and tools == {}

    def test_loads_real_roster(self):
        roles, tools = load_team_data()
        assert set(roles) >= {"genius", "dev", "ctx", "review", "issue"}
        assert "finish" in tools["dev"]


class TestDefaultConfigPath:
    def test_anchors_to_repo_root(self):
        path = default_config_path()
        assert os.path.isfile(path)
        assert path.endswith("agent-config.yaml")

    def test_loadable_from_any_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        roles, _ = load_team_data()
        assert "dev" in roles


class TestLoadAgentSkills:
    def test_returns_comma_list_for_real_roster(self):
        skills = load_agent_skills("dev")
        assert skills == "git-pr-workflow"

    def test_agent_without_skills_returns_empty(self):
        assert load_agent_skills("review") == ""

    def test_unknown_agent_returns_empty(self):
        assert load_agent_skills("nope") == ""

    def test_missing_config_returns_empty(self):
        assert load_agent_skills("dev", "/nonexistent/agent-config.yaml") == ""


class TestBuildTeamEnv:
    def test_writes_shared_context_file(self, tmp_path):
        out = tmp_path / "shared.md"
        env = build_team_env(
            "<!-- bot-agent-genius -->\n@@dev do it",
            role_name="dev",
            shared_context_path=str(out),
        )
        assert env["SHARED_CONTEXT_FILE"] == str(out)
        assert out.is_file()
        content = out.read_text(encoding="utf-8")
        assert "# Your Team" in content
        assert "You were called by @@@genius" in content
        assert json.loads(env["AGENT_ROLES"])["dev"]
        assert "finish" in env["AGENT_TOOLS"].split(",")
        assert env["CALLER_AGENT"] == "genius"

    def test_missing_config_degrades_gracefully(self, tmp_path):
        out = tmp_path / "shared.md"
        env = build_team_env(
            "no marker here",
            role_name="dev",
            config_path="/nonexistent/agent-config.yaml",
            shared_context_path=str(out),
        )
        assert env["AGENT_ROLES"] == ""
        assert env["AGENT_TOOLS"] == ""
        assert env["CALLER_AGENT"] == ""
        assert env["SHARED_CONTEXT_FILE"] == ""
        assert not out.exists()

    def test_default_path_constant(self):
        assert SHARED_CONTEXT_DEFAULT_PATH.startswith("/")
