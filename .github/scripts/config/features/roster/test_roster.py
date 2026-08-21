"""Tests for the agent roster feature."""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import (
    KNOWN_TOOLS,
    STATIC_TASK_TO_PROMPT,
    build_agent_roster,
    validate_roster,
    build_mention_regex,
    extract_caller_agent,
    load_agent_roster,
    load_config,
)


class _FakeAgent:
    def __init__(self, prompt_file):
        self.prompt_file = prompt_file


class _FakeConfig:
    def __init__(self, agents):
        self.agents = agents


class TestBuildAgentRoster:
    def test_builds_name_to_prompt_basename(self):
        config = _FakeConfig({
            "dev": _FakeAgent(".agents/prompts/dev.md"),
            "issue-reviewer": _FakeAgent("prompts/issue-reviewer.md"),
        })
        assert build_agent_roster(config) == {
            "dev": "dev.md",
            "issue-reviewer": "issue-reviewer.md",
        }

    def test_real_config_has_all_nine_agents(self):
        config_path = str(Path(__file__).parents[3] / "agent-config.yaml")
        if not Path(config_path).exists():
            pytest.skip("agent-config.yaml not found")
        roster = build_agent_roster(load_config(config_path))
        expected = set(STATIC_TASK_TO_PROMPT)
        assert set(roster) == expected
        assert all(name.endswith(".md") for name in roster.values())


class TestValidateRoster:
    def _make_dirs(self):
        tmp = Path(tempfile.mkdtemp())
        prompts = tmp / "prompts"
        skills = tmp / "skills"
        prompts.mkdir()
        skills.mkdir()
        return prompts, skills

    def test_valid_roster_has_no_problems(self):
        prompts, skills = self._make_dirs()
        (prompts / "dev.md").write_text("# dev")
        (skills / "git-pr-workflow.md").write_text("# skill")
        problems = validate_roster(
            {"dev": "dev.md"},
            roles={"dev": "Developer."},
            tools_by_agent={"dev": ["terminal", "finish"]},
            skills_by_agent={"dev": ["git-pr-workflow"]},
            prompt_base=str(prompts),
            skills_dir=str(skills),
        )
        assert problems == []

    def test_missing_prompt_file_rejected(self):
        prompts, skills = self._make_dirs()
        problems = validate_roster(
            {"dev": "missing.md"}, prompt_base=str(prompts), skills_dir=str(skills),
        )
        assert any("prompt_file not found" in p for p in problems)

    def test_unknown_tool_rejected(self):
        prompts, skills = self._make_dirs()
        problems = validate_roster(
            {"dev": "dev.md"},
            tools_by_agent={"dev": ["terminal", "teleport"]},
            prompt_base=str(prompts),
            skills_dir=str(skills),
        )
        assert any("unknown tool 'teleport'" in p for p in problems)

    def test_missing_skill_file_rejected(self):
        prompts, skills = self._make_dirs()
        (prompts / "dev.md").write_text("# dev")
        problems = validate_roster(
            {"dev": "dev.md"},
            skills_by_agent={"dev": ["nonexistent-skill"]},
            prompt_base=str(prompts),
            skills_dir=str(skills),
        )
        assert any("skill file not found" in p for p in problems)

    def test_missing_role_rejected_when_roles_given(self):
        problems = validate_roster({"po": "po.md"}, roles={"po": None})
        assert any("missing or empty 'role'" in p for p in problems)


class TestBuildMentionRegex:
    def test_matches_all_roster_names(self):
        roster = dict(STATIC_TASK_TO_PROMPT)
        regex = build_mention_regex(roster)
        for name in roster:
            match = regex.findall(f"hello @@{name} please")
            assert match == [f"@@{name}"], f"@@{name} not extracted"

    def test_hyphenated_key_wins_over_prefix(self):
        roster = dict(STATIC_TASK_TO_PROMPT)
        regex = build_mention_regex(roster)
        assert regex.findall("call @@issue-reviewer now") == ["@@issue-reviewer"]

    def test_unrelated_mention_not_matched(self):
        roster = dict(STATIC_TASK_TO_PROMPT)
        regex = build_mention_regex(roster)
        assert regex.findall("mention @@unknown-agent here") == []


class TestExtractCallerAgent:
    def test_extracts_bot_marker(self):
        assert extract_caller_agent("<!-- bot-agent-dev -->\ntext") == "dev"

    def test_extracts_hyphenated_marker(self):
        assert extract_caller_agent("<!-- bot-agent-issue-reviewer -->") == "issue-reviewer"

    def test_no_marker_returns_none(self):
        assert extract_caller_agent("plain human comment") is None


class TestLoadAgentRoster:
    def test_falls_back_when_config_missing(self, capsys):
        roster = load_agent_roster("/nonexistent/agent-config.yaml")
        assert roster == dict(STATIC_TASK_TO_PROMPT)
        err = capsys.readouterr().err
        assert "static fallback roster" in err

    def test_loads_from_real_config(self):
        config_path = str(Path(__file__).parents[3] / "agent-config.yaml")
        if not Path(config_path).exists():
            pytest.skip("agent-config.yaml not found")
        roster = load_agent_roster(config_path)
        assert "dev" in roster and roster["dev"] == "dev.md"