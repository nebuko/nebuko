"""Unit tests for the team-context feature (issue #476).

Covers the frozen object contract (``TeammateInfo`` / ``TeamContext``), the
rendered section shape, the conditional caller line, and the never-raise
degradation contract shared with ``config.load_agent_names``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.sub_domains.agent_config.agent_config import AgentConfig
from config.sub_domains.app_config.app_config import AppConfig
from config.sub_domains.llm_config.llm_config import LLMConfig
from config.sub_domains.provider_config.provider_config import ProviderConfig
from resolve_config.features.team_context.team_context import (
    SHARED_EXIT_RULES,
    UNDECLARED_ROLE,
    TeamContext,
    build_team_section,
    compose_system_context,
)


_PROVIDER = ProviderConfig(api_url="http://example.test/v1", api_token_env="T")
_LLM = LLMConfig(model_path="hosted_vllm/l1", provider=_PROVIDER)


def _agent(role: str | None, tools: list[str] | None = None) -> AgentConfig:
    return AgentConfig(
        model=_LLM,
        prompt_file="defaults/prompts/x.md",
        role=role,
        tools=tools if tools is not None else ["think", "finish"],
    )


def _config(agents: dict[str, AgentConfig]) -> AppConfig:
    return AppConfig(providers={"p1": _PROVIDER}, llms={"l1": _LLM}, agents=agents)


@pytest.fixture()
def roster() -> AppConfig:
    return _config(
        {
            "dev": _agent("Implementation engineer.", ["terminal", "think"]),
            "architect": _agent("Plans and delegates."),
            "context": _agent("Collects context."),
        }
    )


class TestFromConfig:
    def test_self_is_excluded_from_teammates(self, roster: AppConfig) -> None:
        team = TeamContext.from_config(roster, "dev")
        names = [mate.name for mate in team.teammates]
        assert names == ["architect", "context"]
        assert "dev" not in names

    def test_self_role_and_tools_are_captured(self, roster: AppConfig) -> None:
        team = TeamContext.from_config(roster, "dev")
        assert team.self_name == "dev"
        assert team.self_role == "Implementation engineer."
        assert team.self_tools == ("terminal", "think")

    def test_missing_role_falls_back_to_placeholder(self) -> None:
        config = _config({"dev": _agent(None), "review": _agent("Reviews.")})
        team = TeamContext.from_config(config, "dev")
        assert team.self_role == UNDECLARED_ROLE
        assert team.teammates[0].role == "Reviews."

    def test_unknown_self_name_raises_key_error(self, roster: AppConfig) -> None:
        with pytest.raises(KeyError):
            TeamContext.from_config(roster, "ghost")


class TestRender:
    def test_renders_team_section(self, roster: AppConfig) -> None:
        rendered = TeamContext.from_config(roster, "dev").render()
        assert rendered.startswith("# Your Team")
        assert "You are dev — Implementation engineer." in rendered
        assert "- architect: Plans and delegates." in rendered
        assert "Delegation rules:" in rendered

    def test_caller_line_absent_without_caller(self, roster: AppConfig) -> None:
        rendered = TeamContext.from_config(roster, "dev").render()
        assert "You were called by" not in rendered

    def test_caller_is_never_rendered(self, roster: AppConfig) -> None:
        """Issue #1033: the callback is engine-owned and hidden from the callee."""
        rendered = TeamContext.from_config(roster, "dev", caller="architect").render()
        assert "You were called by" not in rendered


class TestBuildTeamSection:
    def test_reads_the_live_roster(self, tmp_path: Path) -> None:
        config_path = tmp_path / "agent-config.yaml"
        config_path.write_text(_CONFIG_YAML)
        section = build_team_section("dev", config_path=str(config_path))
        assert "# Your Team" in section
        assert "You are dev — The builder." in section
        assert "architect: The planner." in section

    def test_unknown_agent_degrades_to_empty(self, tmp_path: Path) -> None:
        config_path = tmp_path / "agent-config.yaml"
        config_path.write_text(_CONFIG_YAML)
        assert build_team_section("ghost", config_path=str(config_path)) == ""

    def test_unreadable_config_degrades_to_empty(self, tmp_path: Path) -> None:
        assert build_team_section("dev", config_path=str(tmp_path / "missing.yaml")) == ""

    def test_empty_self_name_returns_empty(self, tmp_path: Path) -> None:
        assert build_team_section("", config_path=str(tmp_path / "missing.yaml")) == ""


class TestComposeSystemContext:
    def test_exactly_one_of_each_shared_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / "agent-config.yaml"
        config_path.write_text(_CONFIG_YAML)
        context = compose_system_context("dev", config_path=str(config_path))
        assert context.count("# Strict Output & Exit Rules") == 1
        assert context.count("# Your Team") == 1

    def test_degrades_to_exit_rules_when_roster_unreadable(self, tmp_path: Path) -> None:
        context = compose_system_context("dev", config_path=str(tmp_path / "missing.yaml"))
        assert context == SHARED_EXIT_RULES
        assert "# Your Team" not in context


_CONFIG_YAML = """
providers:
  p1:
    api_url: "http://example.test/v1"
    api_token_env: "P1_TOKEN"
llms:
  l1:
    model_path: "hosted_vllm/l1"
    provider: "${providers.p1}"
agents:
  dev:
    model: "${llms.l1}"
    role: "The builder."
    prompt_file: "defaults/prompts/dev.md"
    tools: ["terminal", "think"]
  architect:
    model: "${llms.l1}"
    role: "The planner."
    prompt_file: "defaults/prompts/architect.md"
    tools: ["think", "finish"]
"""


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_DIR = _REPO_ROOT / "defaults" / "prompts"


class TestPromptBodies:
    """The slimming invariant: prompts hold personality + core rules only.

    The generic exit discipline and the team section are injected at run time
    (``compose_system_context``), so no prompt file may carry its own copy.
    """

    def test_no_prompt_carries_the_generic_exit_rules(self) -> None:
        offenders = [
            prompt.name
            for prompt in sorted(_PROMPTS_DIR.glob("*.md"))
            if "# Strict Output & Exit Rules" in prompt.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"prompts carry a duplicate exit-rules block: {offenders}"

