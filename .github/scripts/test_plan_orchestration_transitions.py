"""End-to-end transition spec for the plan-orchestration chain (issue #671).

Reproduces the ``scaffold -> sub_impl/step_1 -> step_2`` hops of issue #648
that stalled and had to be unstuck with a manual ``/workflow resume``. Every
hop must resolve to the runnable leaf on its own, and consecutive hops must
carry distinct positions so the loop guard can tell them apart (issue #617 /
#671).
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from resolve_workflow import resolve_workflow_step  # noqa: E402

ENTRY_PATH = "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/implement"


def _write(tmp_path: Path, name: str, doc: dict) -> None:
    (tmp_path / f"{name}.yaml").write_text(yaml.dump(doc))


@pytest.fixture
def plan_chain_dir(tmp_path: Path) -> Path:
    """plan-orchestration -> tmp-impl -> dev-review-loop (the #648 shape)."""
    _write(tmp_path, "dev-review-loop", {
        "name": "dev-review-loop",
        "description": "Reusable sub-workflow: implement + review loop",
        "trigger": {"agents": ["dev"]},
        "steps": [
            {"name": "implement", "agent": "dev", "next": "review"},
            {
                "name": "review",
                "agent": "review",
                "conditions": [
                    {"on_result": "approved", "next": None},
                    {"on_result": "rejected", "next": "implement"},
                    {"on_result": "default", "next": "implement"},
                ],
            },
        ],
    })
    _write(tmp_path, "tmp-impl", {
        "name": "tmp-impl",
        "description": "Dynamically generated per-step execution workflow",
        "trigger": {"agents": ["dev"]},
        "steps": [
            {"name": "step_1", "workflow": "dev-review-loop", "next": "step_2"},
            {"name": "step_2", "workflow": "dev-review-loop", "next": None},
        ],
    })
    _write(tmp_path, "plan-orchestration", {
        "name": "plan-orchestration",
        "description": "Top-level orchestration",
        "trigger": {"agents": ["genius"]},
        "steps": [
            {"name": "scaffold", "agent": "genius", "next": "sub_impl"},
            {"name": "sub_impl", "workflow": "tmp-impl", "next": "sub_clean"},
            {"name": "sub_clean", "agent": "dev", "next": None},
        ],
    })
    return tmp_path


def _routing(workflows_dir: str, workflow: str, step: str) -> dict:
    return resolve_workflow_step(
        task="dev",
        workflow_name=workflow,
        step_name=step,
        branch_key="",
        workflows_dir=workflows_dir,
        prompt_base="/tmp/prompts-nonexistent-ok",
        parent_workflow="",
        parent_workflow_step="",
    )


class TestPlanOrchestrationTransitions:
    def test_scaffold_next_hop_enters_step1_leaf(self, plan_chain_dir):
        """The hop that needed a manual resume in #648."""
        result = _routing(str(plan_chain_dir), "plan-orchestration", "sub_impl")

        assert result["SKIP_AGENT"] == "false"
        assert (result["WORKFLOW_NAME"], result["WORKFLOW_STEP"]) == (
            "dev-review-loop",
            "implement",
        )
        assert result["WORKFLOW_POSITION_PATH"] == ENTRY_PATH

    def test_step1_to_step2_resolves_without_resume(self, plan_chain_dir):
        """The second hop that also needed a manual resume in #648."""
        result = _routing(str(plan_chain_dir), "tmp-impl", "step_2")

        assert result["SKIP_AGENT"] == "false"
        assert (result["WORKFLOW_NAME"], result["WORKFLOW_STEP"]) == (
            "dev-review-loop",
            "implement",
        )
        assert result["WORKFLOW_POSITION_PATH"] == (
            "tmp-impl/step_2|dev-review-loop/implement"
        )

    def test_consecutive_hops_have_distinct_positions(self, plan_chain_dir):
        """Same leaf, different chain — the loop guard must tell them apart."""
        step1 = _routing(str(plan_chain_dir), "tmp-impl", "step_1")
        step2 = _routing(str(plan_chain_dir), "tmp-impl", "step_2")

        assert step1["WORKFLOW_POSITION_PATH"] != step2["WORKFLOW_POSITION_PATH"]
