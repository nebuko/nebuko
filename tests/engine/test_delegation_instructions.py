"""Regression guard for the lost ``@@context`` hand-off on analyze (issue #1054).

Routing is typed-only: the hand-off is ``finalize.turn_outcome.agent_call``,
and the prose of the report never routes. The shipped instructions used to
show a ``finalize(turn_outcome={appreciation:…})`` call with no ``agent_call``
(and, in the output-contract variant, a ``finalize(route=…)`` shape the schema
rejects outright). A model that made that call lost its delegation, read the
rejection as "context returned nothing" and planned on the analysis turn.

These tests pin the shipped configuration:

* no instruction shows the invalid ``finalize(route=…)`` shape;
* every delegating analyze step names ``turn_outcome.agent_call``, so the
  canonical call shape is the only one on offer;
* every analyze step demotes ``planning`` to ``discover`` — the plan template
  must not be injected on the turn that must not plan.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nebuko.engine.lib.config import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIRS = (
    REPO_ROOT / "defaults" / "workflows",
    REPO_ROOT / ".nebuko" / "workflows",
)
INSTRUCTION_ROOTS = (REPO_ROOT / "defaults", REPO_ROOT / ".nebuko")
SKILL_FILES = (REPO_ROOT / "defaults" / "skills", REPO_ROOT / ".nebuko" / "skills")

# Steps whose whole purpose is to delegate to @@context.
DELEGATING_STEPS = ("analyze", "architect")
# Workflows that delegate from an analyze/architect step; ``demo-composition``
# has a bare analyze step with no instructions of its own, so it is exempt.
ANALYZING_WORKFLOWS = (
    "defaults/workflows/default.yaml",
    "defaults/workflows/spec-driven-impl.yaml",
    "defaults/workflows/plan-orchestration.yaml",
    ".nebuko/workflows/default-with-output.yaml",
    ".nebuko/workflows/plan-dev-review-cmd.yaml",
)


def _workflow_files() -> list[Path]:
    files: list[Path] = []
    for directory in WORKFLOW_DIRS:
        files.extend(sorted(directory.glob("*.yaml")))
    return files


def _instruction_files() -> list[Path]:
    files: list[Path] = []
    for root in INSTRUCTION_ROOTS:
        files.extend(sorted(root.rglob("*.yaml")))
        files.extend(sorted(root.rglob("*.md")))
    return files


class TestUnambiguousCallShape:
    def test_no_instruction_shows_the_invalid_route_keyword(self):
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _instruction_files()
            if "finalize(route=" in path.read_text(encoding="utf-8")
        ]

        assert offenders == []

    def test_every_delegating_step_names_agent_call(self):
        """Each analyze/architect instruction spells out the typed hand-off."""
        missing: list[str] = []
        for rel in ANALYZING_WORKFLOWS:
            definition = load_workflow(str(REPO_ROOT / rel))
            for step in definition.steps:
                if step.name not in DELEGATING_STEPS:
                    continue
                assert step.prompt, f"{rel}:{step.name} carries no prompt"
                if "agent_call" not in step.prompt:
                    missing.append(f"{rel}:{step.name}")

        assert missing == []

    def test_the_architect_prompt_shows_the_canonical_handoff(self):
        text = (REPO_ROOT / "defaults" / "prompts" / "architect.md").read_text(
            encoding="utf-8"
        )

        assert '"agent_call"' in text
        assert '"agent_name": "context"' in text


class TestPlanningIsScopedOutOfAnalyze:
    @pytest.mark.parametrize("rel", ANALYZING_WORKFLOWS)
    def test_analyze_demotes_planning_to_discover(self, rel: str):
        definition = load_workflow(str(REPO_ROOT / rel))
        analyze = next(
            step for step in definition.steps if step.name in DELEGATING_STEPS
        )

        modes = {
            name: spec.mode
            for spec in analyze.skill or ()
            for name in spec.names
        }
        assert modes.get("planning") == "discover", (
            f"{rel}:{analyze.name} must demote 'planning' to discover so the "
            "plan template is not injected on an analysis turn"
        )

    def test_planning_stays_injected_on_plan_steps(self):
        """The demotion is step-scoped: plan steps keep the Planning skill."""
        definition = load_workflow(str(REPO_ROOT / "defaults" / "workflows" / "default.yaml"))
        plan = next(step for step in definition.steps if step.name == "plan")

        assert plan.skill is None
        agent_configs = sorted((REPO_ROOT / "defaults" / "agents").glob("agent-config-*.yaml"))
        assert agent_configs, "no agent roster found"
        for path in agent_configs:
            spec = yaml.safe_load(path.read_text(encoding="utf-8"))
            entries = spec["agents"]["architect"]["skills"]
            injected = {
                name
                for entry in entries
                if isinstance(entry, dict) and "inject" in entry
                for name in entry["inject"]
            }
            assert "planning" in injected, f"{path.name} drops the Planning skill"


def test_skill_files_referenced_by_the_demotion_exist():
    names = set()
    for directory in SKILL_FILES:
        names |= {path.stem for path in directory.glob("*.md")}

    assert "planning" in names
