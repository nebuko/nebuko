"""Tests for v2 WORKFLOW_POSITION emission (issue #618).

Covers the review round-2 blocking items:
- ``_build_position_chain`` / ``_position_to_json`` / ``_position_pair``
  behavior (cycle, depth cap, unresolvable ref, missing dynamic child,
  JSON shape parity with ``WorkflowPosition.to_state_dict``).
- Entry-path emission: both ``WORKFLOW_POSITION`` and
  ``WORKFLOW_POSITION_PATH`` land on the resolver result (agent + script).
- Centralized emission: non-entry runs inside a sub-workflow get the full
  chain rebuilt from the leaf + parent pairs (``_attach_position``).
- #617 regression: dev run entering from ``scaffold`` gets leaf
  ``dev-review-loop/implement`` and its ``advance`` routes to ``review``.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from resolve_workflow import (  # noqa: E402
    _build_position_chain,
    _position_pair,
    _position_path_str,
    _position_to_json,
    build_workflow_position,
    resolve_workflow_step,
)
from config import POSITION_STATE_VERSION  # noqa: E402
from config import WorkflowPosition  # noqa: E402


def _write(tmp_path: Path, name: str, doc: dict) -> None:
    (tmp_path / f"{name}.yaml").write_text(yaml.dump(doc))


def _dev_review_loop() -> dict:
    return {
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
    }


@pytest.fixture
def scaffold_dir(tmp_path: Path) -> Path:
    """The full #617 shape: plan-orchestration -> tmp-impl -> dev-review-loop."""
    _write(tmp_path, "dev-review-loop", _dev_review_loop())
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
            {"name": "sub_impl", "workflow": "tmp-impl", "next": None},
        ],
    })
    return tmp_path


def _routing(workflows_dir: str, **kw):
    """``resolve_workflow_routing`` via the ``resolve_workflow_step`` facade."""
    base = {
        "task": "dev",
        "complexity": "medium",
        "reasoning": "",
        "model": "",
        "temperature": "",
        "round": "",
        "skills": "",
        "top_p": "",
        "top_k": "",
        "repetition_penalty": "",
        "workflow": "",
        "step": "",
        "branch": "",
        "parent_workflow": "",
        "parent_workflow_step": "",
    }
    base.update(kw)
    return resolve_workflow_step(
        task=base["task"],
        workflow_name=base["workflow"],
        step_name=base["step"],
        branch_key=base["branch"],
        workflows_dir=workflows_dir,
        prompt_base="/tmp/prompts-nonexistent-ok",
        parent_workflow=base["parent_workflow"],
        parent_workflow_step=base["parent_workflow_step"],
    )


class TestPositionChainBuilder:
    def test_full_mount_chain_paths_and_json(self, scaffold_dir):
        wf_dir = str(scaffold_dir)
        # Entry chain from plan-orchestration/sub_impl (outer path known).
        start_path = str(scaffold_dir / "plan-orchestration.yaml")
        chain = _build_position_chain(
            "tmp-impl", "plan-orchestration", "sub_impl",
            {}, {"plan-orchestration"}, wf_dir, None,
            start_path=start_path,
        )
        assert [n[0] for n in chain] == [
            "plan-orchestration", "tmp-impl", "dev-review-loop",
        ]
        assert [n[2] for n in chain] == ["sub_impl", "step_1", "implement"]
        # Outermost node carries the caller-known path (review item 4).
        assert chain[0][1] == start_path
        assert chain[1][1] == "tmp-impl"  # loader-resolved mount ref
        pair = _position_pair(chain)
        assert pair["WORKFLOW_POSITION_PATH"] == (
            "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/implement"
        )
        doc = json.loads(pair["WORKFLOW_POSITION"])
        assert doc["version"] == POSITION_STATE_VERSION
        assert doc["workflow"] == "dev-review-loop"
        assert doc["step"] == "implement"

    def test_unresolvable_mount_ref_stops_chain(self, scaffold_dir):
        wf_dir = str(scaffold_dir)
        chain = _build_position_chain(
            "does-not-exist", "outer", "mount_step",
            {}, {"outer"}, wf_dir, None,
        )
        assert [(n[0], n[2]) for n in chain] == [("outer", "mount_step")]

    def test_cyclic_mount_ref_stops(self, tmp_path):
        _write(tmp_path, "a", {
            "name": "a", "description": "cycle a",
            "trigger": {"agents": ["dev"]},
            "steps": [{"name": "go", "workflow": "b", "next": None}],
        })
        _write(tmp_path, "b", {
            "name": "b", "description": "cycle b",
            "trigger": {"agents": ["dev"]},
            "steps": [{"name": "back", "workflow": "a", "next": None}],
        })
        wf_dir = str(tmp_path)
        chain = _build_position_chain(
            "b", "outer", "mount", {}, {"outer"}, wf_dir, None,
        )
        names = [n[0] for n in chain]
        assert names.count("a") <= 1 and names.count("b") <= 1

    def test_missing_dynamic_child_degrades_to_bare(self, tmp_path):
        _write(tmp_path, "top", {
            "name": "top", "description": "top",
            "trigger": {"agents": ["dev"]},
            "steps": [{"name": "mount", "workflow": "tmp-impl", "next": None}],
        })
        wf_dir = str(tmp_path)
        chain = _build_position_chain(
            "tmp-impl", "top", "mount", {}, {"top"}, wf_dir, None,
        )
        assert [(n[0], n[2]) for n in chain] == [("top", "mount")]
        pair = _position_pair(chain)
        assert pair["WORKFLOW_POSITION_PATH"] == "top/mount"

    def test_json_shape_parity_with_to_state_dict(self, scaffold_dir):
        """``_position_to_json`` must serialize what the posted state
        rehydrates via ``WorkflowPosition.from_state_dict``."""
        chain = [
            ("plan-orchestration", "p", "sub_impl"),
            ("tmp-impl", "r", "step_1"),
            ("dev-review-loop", "q", "implement"),
        ]
        doc = json.loads(_position_to_json(chain))
        pos = WorkflowPosition.from_state_dict(doc)
        assert pos is not None
        assert pos.refs[0].workflow == "plan-orchestration"
        assert pos.leaf().workflow == "dev-review-loop"
        assert pos.leaf().step == "implement"
        # Round trip preserves the flat v3 shape (incl. leaf aliases).
        assert pos.to_state_dict()["workflow"] == "dev-review-loop"
        assert pos.to_state_dict()["step"] == "implement"

    def test_path_str_and_pair_empty_chain(self):
        assert _position_path_str([]) == ""
        pair = _position_pair([])
        assert pair == {"WORKFLOW_POSITION": "", "WORKFLOW_POSITION_PATH": ""}


class TestEntryPathEmission:
    """The entry run must carry BOTH the JSON block and the flat path."""

    def test_entry_agent_result_carries_position_and_path(self, scaffold_dir):
        r = _routing(
            str(scaffold_dir),
            workflow="plan-orchestration", step="sub_impl",
        )
        assert r["ROLE_NAME"] == "dev"
        assert r["WORKFLOW_NAME"] == "dev-review-loop"
        assert r["WORKFLOW_STEP"] == "implement"
        assert r["WORKFLOW_POSITION"], "entry must post the v2 position block"
        assert r["WORKFLOW_POSITION_PATH"] == (
            "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/implement"
        )
        doc = json.loads(r["WORKFLOW_POSITION"])
        assert doc["version"] == POSITION_STATE_VERSION
        assert doc["step"] == "implement"

    def test_entry_from_scaffold_enters_leaf_not_scaffold(self, scaffold_dir):
        """#617 regression: dev run entering from the scaffold phase lands
        on the leaf ``dev-review-loop/implement`` — never re-selecting the
        terminal ``scaffold`` step."""
        r = _routing(
            str(scaffold_dir),
            workflow="tmp-impl", step="step_1",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert (r["WORKFLOW_NAME"], r["WORKFLOW_STEP"]) == (
            "dev-review-loop", "implement",
        )
        assert r["WORKFLOW_POSITION_PATH"].endswith(
            "tmp-impl/step_1|dev-review-loop/implement"
        )

    def test_step1_to_step2_reentry_has_distinct_path(self, scaffold_dir):
        """The #617 bug-class one boundary later: same leaf, different
        chain — the guard must be able to tell them apart."""
        r1 = _routing(str(scaffold_dir), workflow="tmp-impl", step="step_1")
        r2 = _routing(str(scaffold_dir), workflow="tmp-impl", step="step_2")
        assert (r1["WORKFLOW_NAME"], r1["WORKFLOW_STEP"]) == (
            r2["WORKFLOW_NAME"], r2["WORKFLOW_STEP"],
        )
        assert r1["WORKFLOW_POSITION_PATH"] != r2["WORKFLOW_POSITION_PATH"]
        assert r2["WORKFLOW_POSITION_PATH"] == (
            "tmp-impl/step_2|dev-review-loop/implement"
        )


class TestNonEntryEmission:
    """Runs *inside* a sub-workflow (no entry descent) rebuild the chain
    from the leaf + parent pairs via ``_attach_position`` in
    ``_build_result`` — the single choke point every agent run passes."""

    @staticmethod
    def _build(workflows_dir: str, **cfg) -> dict:
        from resolve_config.features.build_result.build_result import (
            _build_result,
        )
        base = {
            "task": "dev",
            "config_complexity": "medium",
            "config_reasoning": "none",
            "config_round": "",
            "config_skills": "",
        }
        base.update(cfg)
        prompt_base = tempfile.mkdtemp()
        from resolve_workflow import TASK_TO_PROMPT
        for name in set(TASK_TO_PROMPT.values()):
            (Path(prompt_base) / name).write_text("# prompt")
        return _build_result(base, prompt_base, workflows_dir)

    def test_review_run_carries_full_chain(self, scaffold_dir):
        """A run inside ``dev-review-loop`` with the recorded parent pair
        posts the full mount chain — the run whose ``position=None`` state
        made #617 recur one boundary later."""
        r = self._build(
            str(scaffold_dir),
            config_workflow="dev-review-loop",
            config_step="implement",
            config_parent_workflow="tmp-impl",
            config_parent_workflow_step="step_1",
        )
        assert r["WORKFLOW_POSITION_PATH"] == (
            "tmp-impl/step_1|dev-review-loop/implement"
        )
        doc = json.loads(r["WORKFLOW_POSITION"])
        assert doc["version"] == POSITION_STATE_VERSION
        assert doc["workflow"] == "dev-review-loop"

    def test_top_level_run_stays_position_less(self, scaffold_dir):
        """No recorded parent pair (top-level workflow) → v1 position-less."""
        r = self._build(
            str(scaffold_dir),
            config_workflow="plan-orchestration",
            config_step="scaffold",
        )
        assert r.get("WORKFLOW_POSITION", "") in ("", None)
        assert r.get("WORKFLOW_POSITION_PATH", "") in ("", None)

    def test_entry_emission_kept_verbatim(self, scaffold_dir):
        """When the resolver's entry path already emitted a position,
        ``_attach_position`` keeps it untouched (fidelity over rebuild)."""
        r = self._build(
            str(scaffold_dir),
            config_workflow="plan-orchestration",
            config_step="sub_impl",
        )
        assert r["WORKFLOW_POSITION_PATH"] == (
            "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/implement"
        )

    def test_build_workflow_position_leaf_substitution(self, scaffold_dir):
        pair = build_workflow_position(
            "dev-review-loop", "review", "tmp-impl", "step_1",
            str(scaffold_dir),
        )
        assert pair["WORKFLOW_POSITION_PATH"] == (
            "tmp-impl/step_1|dev-review-loop/review"
        )
        doc = json.loads(pair["WORKFLOW_POSITION"])
        assert doc["step"] == "review"

    def test_build_workflow_position_missing_dynamic_child(self, scaffold_dir):
        """Parent references an absent dynamic workflow: the chain stops at
        the recorded parent node (identity preserved) and the leaf is
        appended — never failing resolution."""
        pair = build_workflow_position(
            "dev-review-loop", "review", "ghost-impl", "step_1",
            str(scaffold_dir),
        )
        assert pair["WORKFLOW_POSITION_PATH"] == (
            "ghost-impl/step_1|dev-review-loop/review"
        )
        doc = json.loads(pair["WORKFLOW_POSITION"])
        assert doc["step"] == "review"
