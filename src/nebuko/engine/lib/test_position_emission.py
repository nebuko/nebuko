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


from nebuko.engine.lib.resolve_workflow import (
    _build_position_chain,
    _position_pair,
    _position_path_str,
    _position_to_json,
    build_workflow_position,
    resolve_workflow_step,
)
from nebuko.engine.lib.config import POSITION_STATE_VERSION
from nebuko.engine.lib.config import WorkflowPosition


def _write(tmp_path: Path, name: str, doc: dict) -> None:
    (tmp_path / f"{name}.yaml").write_text(yaml.dump(doc))


def _dev_review_loop() -> dict:
    return {
        "name": "dev-review-loop",
        "description": "Reusable sub-workflow: implement + review loop",
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
        "steps": [
            {"name": "step_1", "workflow": "dev-review-loop", "next": "step_2"},
            {"name": "step_2", "workflow": "dev-review-loop", "next": None},
        ],
    })
    _write(tmp_path, "plan-orchestration", {
        "name": "plan-orchestration",
        "description": "Top-level orchestration",
        "steps": [
            {"name": "scaffold", "agent": "architect", "next": "sub_impl"},
            {"name": "sub_impl", "workflow": "tmp-impl", "next": "sub_clean"},
            {"name": "sub_clean", "agent": "dev", "next": None},
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
            "steps": [{"name": "go", "workflow": "b", "next": None}],
        })
        _write(tmp_path, "b", {
            "name": "b", "description": "cycle b",
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
        from nebuko.engine.lib.resolve_config.features.build_result.build_result import (
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
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT
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

    def test_top_level_run_emits_bare_position(self, scaffold_dir):
        """No recorded parent pair (top-level workflow) → bare single-ref
        position, never empty (issue #646)."""
        r = self._build(
            str(scaffold_dir),
            config_workflow="plan-orchestration",
            config_step="scaffold",
        )
        assert r["WORKFLOW_POSITION_PATH"] == "plan-orchestration/scaffold"
        doc = json.loads(r["WORKFLOW_POSITION"])
        assert doc["version"] == POSITION_STATE_VERSION
        assert doc["workflow"] == "plan-orchestration"
        assert doc["step"] == "scaffold"
        assert doc["refs"] == [
            {"workflow": "plan-orchestration", "step": "scaffold"}
        ]

    def test_build_workflow_position_top_level_bare(self, scaffold_dir):
        """Top-level run (no parent pair): the bare single-ref pair."""
        pair = build_workflow_position(
            "plan-orchestration", "scaffold", "", "", str(scaffold_dir),
        )
        assert pair["WORKFLOW_POSITION_PATH"] == "plan-orchestration/scaffold"
        doc = json.loads(pair["WORKFLOW_POSITION"])
        assert doc["version"] == POSITION_STATE_VERSION
        assert doc["refs"] == [
            {"workflow": "plan-orchestration", "step": "scaffold"}
        ]

    def test_build_workflow_position_no_leaf_stays_empty(self, scaffold_dir):
        """No leaf resolvable at all → empty pair (positionless)."""
        pair = build_workflow_position(
            "", "", "", "", str(scaffold_dir),
        )
        assert pair == {"WORKFLOW_POSITION": "", "WORKFLOW_POSITION_PATH": ""}

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


class TestCarryForwardEmission:
    """Issue #812: a non-entry transition must carry the previous run's full
    mount stack forward and mutate one frame — never rebuild it from the
    lossy flat parent pair, which truncated a nested run at every
    intra-workflow step move."""

    @staticmethod
    def _build(workflows_dir: str, position=None, **cfg) -> dict:
        from nebuko.engine.lib.resolve_config.features.build_result.build_result import (
            _build_result,
        )
        base = {
            "task": "dev",
            "config_complexity": "medium",
            "config_reasoning": "none",
            "config_round": "",
            "config_skills": "",
        }
        if position is not None:
            base["config_position"] = position
        base.update(cfg)
        prompt_base = tempfile.mkdtemp()
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT
        for name in set(TASK_TO_PROMPT.values()):
            (Path(prompt_base) / name).write_text("# prompt")
        return _build_result(base, prompt_base, workflows_dir)

    @staticmethod
    def _position(*pairs) -> WorkflowPosition:
        from nebuko.engine.lib.config import StepRef
        return WorkflowPosition(
            refs=[StepRef(workflow=w, step=s) for w, s in pairs]
        )

    def test_with_step_replaces_leaf_and_keeps_ancestors(self):
        from nebuko.engine.lib.config import StepRef
        pos = self._position(
            ("plan-orchestration", "sub_impl"),
            ("tmp-impl", "step_1"),
            ("dev-review-loop", "implement"),
        )
        moved = pos.with_step(StepRef(workflow="dev-review-loop", step="review"))
        assert moved.path_str() == (
            "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/review"
        )
        # Immutable value object: the original leaf is untouched.
        assert pos.path_str().endswith("dev-review-loop/implement")

    def test_with_step_empty_position_raises(self):
        from nebuko.engine.lib.config import StepRef
        with pytest.raises(ValueError):
            WorkflowPosition().with_step(StepRef(workflow="w", step="s"))

    def test_intra_workflow_move_keeps_full_stack(self, scaffold_dir):
        """``implement -> review`` inside ``dev-review-loop`` crosses no mount
        boundary (empty parent), so only the leaf frame is replaced and every
        ancestor survives. Rebuilding from the empty pair truncated this to a
        bare ``dev-review-loop/review`` (issue #812)."""
        base = self._position(
            ("plan-orchestration", "sub_impl"),
            ("tmp-impl", "step_1"),
            ("dev-review-loop", "implement"),
        )
        r = self._build(
            str(scaffold_dir),
            position=base,
            config_workflow="dev-review-loop",
            config_step="review",
        )
        assert r["WORKFLOW_POSITION_PATH"] == (
            "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/review"
        )
        assert json.loads(r["WORKFLOW_POSITION"])["step"] == "review"

    def test_terminal_exit_collapses_to_outer_successor(self, scaffold_dir):
        """A terminal inner workflow re-enters the outer workflow's next step
        (leaf workflow differs, empty parent): the stack collapses to the
        outermost frame rather than keeping the stale inner frames."""
        base = self._position(
            ("plan-orchestration", "sub_impl"),
            ("tmp-impl", "step_10"),
            ("dev-review-loop", "review"),
        )
        r = self._build(
            str(scaffold_dir),
            position=base,
            config_workflow="plan-orchestration",
            config_step="sub_clean",
        )
        assert r["WORKFLOW_POSITION_PATH"] == "plan-orchestration/sub_clean"

    def test_mount_reentry_splices_under_anchor(self, scaffold_dir):
        """Re-entry into a deeper mount (resolved parent pair) splices the
        fresh tail beneath the anchors that enclose it — the branch-mounted
        ``tmp-impl`` frame is retained even though the flat pair only records
        one level (issue #812)."""
        base = self._position(
            ("plan-orchestration", "sub_impl"),
            ("tmp-impl", "step_1"),
            ("dev-review-loop", "review"),
        )
        r = self._build(
            str(scaffold_dir),
            position=base,
            config_workflow="dev-review-loop",
            config_step="implement",
            config_parent_workflow="tmp-impl",
            config_parent_workflow_step="step_2",
        )
        assert r["WORKFLOW_POSITION_PATH"] == (
            "plan-orchestration/sub_impl|tmp-impl/step_2|dev-review-loop/implement"
        )

    def test_absent_base_falls_back_to_pair_rebuild(self, scaffold_dir):
        """No carried-forward base (legacy/absent state) still rebuilds from
        the recorded pair, preserving the pre-#812 contract."""
        r = self._build(
            str(scaffold_dir),
            config_workflow="dev-review-loop",
            config_step="review",
            config_parent_workflow="tmp-impl",
            config_parent_workflow_step="step_1",
        )
        assert r["WORKFLOW_POSITION_PATH"] == (
            "tmp-impl/step_1|dev-review-loop/review"
        )
