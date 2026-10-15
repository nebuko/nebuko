"""``SkillContext``: one step's inject / discover attachment (issue #955).

Injectable skills are rendered into the assembled prompt, discoverable ones are
handed to the SDK as a name + summary catalog that the agent reads on demand.
Both consumers (prompt builder, agent context builder) resolve through this one
object, so the tests pin its merge rules, its rendering, and the SDK hand-off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config import SkillSpec
from resolve_config.features.skill_context.skill_context import (
    AGENT_SKILL_SPECS_ENV,
    SKILLS_DIR_ENV,
    STEP_SKILL_SPECS_ENV,
    SkillContext,
    resolve_skill_path,
    skill_context_from_env,
    specs_from_env,
    specs_to_env,
)

FRONTMATTER_SKILL = (
    "---\n"
    'description: "One-line summary of alpha."\n'
    "---\n"
    "\n"
    "# Alpha Skill\n"
    "\n"
    "ALPHA BODY\n"
)

PLAIN_SKILL = "# Beta Skill\n\nBeta summary line.\n\nBETA BODY\n"


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "skills"
    directory.mkdir()
    (directory / "alpha.md").write_text(FRONTMATTER_SKILL, encoding="utf-8")
    (directory / "beta.md").write_text(PLAIN_SKILL, encoding="utf-8")
    return directory


def _specs(*entries):
    return SkillSpec.parse_all(list(entries))


class TestResolve:
    def test_agent_and_step_entries_are_unioned(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"inject": ["alpha"]}),
            _specs({"discover": ["beta"]}),
            skills_dir=str(skills_dir),
        )

        assert [doc.name for doc in context.injected] == ["alpha"]
        assert [doc.name for doc in context.discoverable] == ["beta"]

    def test_step_mode_wins_on_a_name_clash(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"inject": ["alpha"]}),
            _specs({"discover": ["alpha"]}),
            skills_dir=str(skills_dir),
        )

        assert [doc.name for doc in context.injected] == []
        assert [doc.name for doc in context.discoverable] == ["alpha"]

    def test_agent_order_comes_first_then_step_only_names(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"inject": ["alpha"]}, {"inject": ["beta"]}),
            _specs({"inject": ["beta"]}),
            skills_dir=str(skills_dir),
        )

        assert [doc.name for doc in context.injected] == ["alpha", "beta"]

    def test_an_unreadable_skill_is_dropped(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"inject": ["alpha", "ghost"]}),
            None,
            skills_dir=str(skills_dir),
        )

        assert [doc.name for doc in context.injected] == ["alpha"]

    def test_no_specs_is_empty(self, skills_dir: Path):
        context = SkillContext.resolve(None, None, skills_dir=str(skills_dir))

        assert context == SkillContext()
        assert context.needs_invoke_tool is False
        assert context.render_injected() == ""


class TestRenderInjected:
    def test_renders_the_full_body_without_frontmatter(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"inject": ["alpha"]}), None, skills_dir=str(skills_dir)
        )

        rendered = context.render_injected()

        assert rendered.startswith("## Injected skills")
        assert "### alpha" in rendered
        assert "ALPHA BODY" in rendered
        assert "description:" not in rendered

    def test_each_skill_gets_its_own_section(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"inject": ["alpha", "beta"]}), None, skills_dir=str(skills_dir)
        )

        rendered = context.render_injected()

        assert rendered.index("### alpha") < rendered.index("### beta")
        assert "BETA BODY" in rendered

    def test_discoverable_skills_are_not_rendered(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"discover": ["alpha"]}), None, skills_dir=str(skills_dir)
        )

        assert context.render_injected() == ""
        assert context.needs_invoke_tool is True


class TestSummaries:
    def test_frontmatter_description_wins(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"discover": ["alpha"]}), None, skills_dir=str(skills_dir)
        )

        assert context.discoverable[0].summary == "One-line summary of alpha."

    def test_falls_back_to_the_first_non_heading_line(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"discover": ["beta"]}), None, skills_dir=str(skills_dir)
        )

        assert context.discoverable[0].summary == "Beta summary line."


class TestSdkSkills:
    def test_description_is_stamped_from_the_summary(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"discover": ["alpha", "beta"]}), None, skills_dir=str(skills_dir)
        )

        skills = context.to_sdk_skills()

        assert [skill.name for skill in skills] == ["alpha", "beta"]
        assert skills[0].description == "One-line summary of alpha."
        assert skills[1].description == "Beta summary line."

    def test_agentskills_format_keeps_the_body_out_of_the_prompt(
        self, skills_dir: Path
    ):
        """The catalog lists name + description only; the body stays on demand."""
        from openhands.sdk.context import AgentContext

        context = SkillContext.resolve(
            _specs({"discover": ["alpha"]}), None, skills_dir=str(skills_dir)
        )
        agent_context = AgentContext(
            system_message_suffix="SUFFIX", skills=context.to_sdk_skills()
        )

        suffix = agent_context.get_system_message_suffix() or ""

        assert "<available_skills>" in suffix
        assert "<name>alpha</name>" in suffix
        assert "One-line summary of alpha." in suffix
        assert "ALPHA BODY" not in suffix
        assert "SUFFIX" in suffix

    def test_injected_skills_are_not_handed_to_the_sdk(self, skills_dir: Path):
        context = SkillContext.resolve(
            _specs({"inject": ["alpha"]}), None, skills_dir=str(skills_dir)
        )

        assert context.to_sdk_skills() == []


class TestEnvHandoff:
    def test_round_trips_both_levels(self, skills_dir: Path):
        agent_specs = _specs({"inject": ["alpha"]})
        step_specs = _specs({"discover": ["beta"]})

        context = skill_context_from_env(
            {
                AGENT_SKILL_SPECS_ENV: specs_to_env(agent_specs),
                STEP_SKILL_SPECS_ENV: specs_to_env(step_specs),
                SKILLS_DIR_ENV: str(skills_dir),
            }
        )

        assert [doc.name for doc in context.injected] == ["alpha"]
        assert [doc.name for doc in context.discoverable] == ["beta"]

    def test_empty_payload_is_empty(self):
        assert specs_to_env([]) == ""
        assert specs_from_env("") == []
        assert skill_context_from_env({}) == SkillContext()

    def test_malformed_payload_is_empty(self):
        assert specs_from_env("not json") == []
        assert specs_from_env('{"inject": ["a"]}') == []
        assert specs_from_env('[{"mode": "watch", "names": ["a"]}]') == []


class TestResolveSkillPath:
    def test_bare_name_resolves_to_markdown(self, skills_dir: Path):
        assert resolve_skill_path("alpha", str(skills_dir)) == str(
            skills_dir / "alpha.md"
        )

    def test_explicit_path_passes_through(self, skills_dir: Path):
        explicit = skills_dir / "alpha.md"

        assert resolve_skill_path(str(explicit), str(skills_dir)) == str(explicit)

    def test_missing_name_returns_the_last_candidate(self, skills_dir: Path):
        assert resolve_skill_path("ghost", str(skills_dir)) == str(
            skills_dir / "ghost.md"
        )

    def test_workflow_generated_path_resolves_relative_to_the_checkout(
        self, tmp_path: Path, monkeypatch
    ):
        """A plan step's `tmp_impl/skills/step_1` lives in the repo, not skills/."""
        generated = tmp_path / "tmp_impl" / "skills"
        generated.mkdir(parents=True)
        (generated / "step_1.md").write_text(PLAIN_SKILL, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert resolve_skill_path("tmp_impl/skills/step_1") == str(
            generated / "step_1.md"
        )


class TestShippedRoster:
    """The shipped configs must resolve through the real shipped skill files."""

    def test_architect_skills_resolve(self):
        from config import load_config

        agent = load_config("agent-config-opencode.yaml").agents["architect"]
        specs = list(agent.skills)
        context = SkillContext.resolve(specs)

        assert [doc.name for doc in context.injected] == [
            "call-agent",
            "posting-contract",
            "planning",
        ]
        assert [doc.name for doc in context.discoverable] == ["complexity"]
        assert all(doc.summary for doc in context.discoverable)
