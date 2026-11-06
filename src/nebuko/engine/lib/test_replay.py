"""Tests for the deterministic replay helper (issue #1314)."""

import json
from datetime import datetime, timezone

import pytest
import yaml

from nebuko.engine.lib import replay
from nebuko.engine.lib.config import StepRef
from nebuko.engine.lib.replay import (
    load_state_from_text,
    main,
    rebuild_prev_state,
    render_table,
    replay_entries,
    replay_entry,
    trigger_body_for,
)
from nebuko.engine.lib.workflow_state import (
    GateDecision,
    build_state,
)

PROMPT_BASE = "defaults/prompts"


def _linear_workflow(tmp_path):
    (tmp_path / "wf.yaml").write_text(
        yaml.dump(
            {
                "name": "wf",
                "description": "linear test workflow",
                "steps": [
                    {"name": "analyze", "agent": "architect", "next": "plan"},
                    {
                        "name": "plan",
                        "agent": "architect",
                        "complexity": "high",
                        "next": "implement",
                    },
                    {"name": "implement", "agent": "dev", "next": None},
                ],
            }
        )
    )
    return tmp_path


def _gated_workflow(tmp_path):
    (tmp_path / "gated.yaml").write_text(
        yaml.dump(
            {
                "name": "gated",
                "description": "gated test workflow",
                "steps": [
                    {
                        "name": "plan",
                        "agent": "architect",
                        "human_gate": True,
                        "next": "implement",
                    },
                    {"name": "implement", "agent": "dev", "next": None},
                ],
            }
        )
    )
    return tmp_path


def _state(workflow="wf", entries=("analyze", "plan", "implement")):
    """A run whose history is one approved entry per name.

    Recorded metrics carry the tier the live run actually used (the *effective*
    tier, post-overlay), exactly like a real run's state.
    """
    tiers = {"plan": "high"}
    state = None
    for step in entries:
        agent = "dev" if step == "implement" else "architect"
        state = build_state(
            state,
            workflow=workflow,
            step=step,
            agent=agent,
            position=[{"workflow": workflow, "step": step}],
            metrics_dict={"complexity": tiers.get(step, "medium")},
            turn_outcome=replay_turn_outcome(),
        )
    return state


def replay_turn_outcome():
    from nebuko.engine.lib.workflow_state import Appreciation, TurnOutcome, TurnStatus

    return TurnOutcome(status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED)


class TestLoadState:
    def test_bare_json_document(self):
        state = _state()
        loaded = load_state_from_text(state.model_dump_json())
        assert loaded is not None
        assert [entry.step for entry in loaded.history] == [
            "analyze",
            "plan",
            "implement",
        ]

    def test_marked_comment_body(self):
        state = _state()
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            + state.model_dump_json()
            + "\n<!-- WORKFLOW_STATE_END -->\n"
        )
        loaded = load_state_from_text(body)
        assert loaded is not None
        assert loaded.workflow == "wf"

    def test_wrapper_object(self):
        state = _state()
        loaded = load_state_from_text(json.dumps({"state": json.loads(state.model_dump_json())}))
        assert loaded is not None
        assert loaded.step == "implement"

    def test_unparseable_returns_none(self):
        assert load_state_from_text("not a state") is None
        assert load_state_from_text("") is None


class TestRebuildPrevState:
    def test_seed_entry_has_no_prior_state(self):
        assert rebuild_prev_state(_state(), 0, "defaults/workflows") is None

    def test_rebuilds_the_prefix_before_the_entry(self, tmp_path):
        workflows = _linear_workflow(tmp_path)
        state = _state()
        prev = rebuild_prev_state(state, 2, str(workflows))
        assert prev is not None
        assert [entry.step for entry in prev.history] == ["analyze", "plan"]
        assert (prev.workflow, prev.step, prev.agent) == ("wf", "plan", "architect")
        assert prev.turn_outcome is not None
        assert prev.turn_outcome.appreciation.value == "approved"

    def test_out_of_range_raises(self):
        with pytest.raises(IndexError):
            rebuild_prev_state(_state(), 9, "defaults/workflows")

    def test_trigger_body_round_trips(self):
        state = _state()
        prev = rebuild_prev_state(state, 1, "defaults/workflows")
        reparsed = load_state_from_text(trigger_body_for(prev))
        assert reparsed is not None
        assert reparsed.step == "analyze"


class TestGateReconstruction:
    def _gated_state(self, decisions=()):
        state = build_state(
            None,
            workflow="gated",
            step="plan",
            agent="architect",
            position=[{"workflow": "gated", "step": "plan"}],
            comment_ids=[100],
            turn_outcome=replay_turn_outcome(),
        )
        state = build_state(
            state,
            workflow="gated",
            step="implement",
            agent="dev",
            position=[{"workflow": "gated", "step": "implement"}],
            turn_outcome=replay_turn_outcome(),
        )
        return state.model_copy(update={"gate_history": list(decisions)})

    def test_armed_gate_is_rebuilt_for_a_gated_step(self, tmp_path):
        workflows = _gated_workflow(tmp_path)
        prev = rebuild_prev_state(self._gated_state(), 1, str(workflows))
        assert prev is not None
        assert prev.gate is not None
        assert prev.gate.step == "plan"
        assert prev.gate.armed()

    def test_recorded_decision_releases_the_gate(self, tmp_path):
        workflows = _gated_workflow(tmp_path)
        decision = GateDecision(
            status="approved",
            workflow="gated",
            target_step="plan",
            decided_by="human",
            decided_at=datetime.now(timezone.utc),
            comment_id=101,
            position=[StepRef(workflow="gated", step="plan")],
        )
        prev = rebuild_prev_state(
            self._gated_state([decision]), 1, str(workflows)
        )
        assert prev is not None
        assert prev.gate is None

    def test_replay_of_a_gated_step_holds(self, tmp_path):
        workflows = _gated_workflow(tmp_path)
        row = replay_entry(
            self._gated_state(),
            1,
            workflows_dir=str(workflows),
            prompt_base=PROMPT_BASE,
        )
        assert "human_gate_hold" in row.skip_kind
        assert "human_gate:plan held" in row.note
        assert (row.step, row.agent) == ("implement", "dev")


class TestReplayEntry:
    def test_per_entry_tiers_and_routing(self, tmp_path):
        workflows = _linear_workflow(tmp_path)
        rows = replay_entries(
            _state(),
            workflows_dir=str(workflows),
            prompt_base=PROMPT_BASE,
        )
        assert [row.entry for row in rows] == [0, 1, 2]
        assert rows[0].note.startswith("seed invocation")

        plan = rows[1]
        assert (plan.step, plan.agent) == ("plan", "architect")
        # The step declares ``complexity: high``; the boundary drift check then
        # downgrades the evidence-free synthetic run by one tier, exactly as the
        # live path would — the table shows both facts.
        assert plan.base_tier == "high"
        assert plan.replay_delta == -1
        assert plan.trigger == "evidence-drift"
        assert plan.config_complexity == "medium"
        assert (plan.next_step, plan.next_agent) == ("implement", "dev")
        assert plan.matches

        implement = rows[2]
        assert implement.base_tier == "medium"
        assert implement.next_step == ""

    def test_recorded_adjustment_inverts_to_the_declared_base(self, tmp_path):
        from nebuko.engine.lib.complexity_governor import (
            ComplexityAdjustment,
            EvidenceBundle,
            HumanSignals,
        )

        workflows = _linear_workflow(tmp_path)
        adjustment = ComplexityAdjustment(
            delta=1,
            trigger="evidence-drift",
            evidence=EvidenceBundle(human=HumanSignals(changes_requested=3)),
            rationale="evidence drift medium -> high",
        )
        state = _state()
        history = list(state.history)
        history[1] = history[1].model_copy(
            update={
                "metrics": history[1].metrics.model_copy(update={"complexity": "high"}),
                "complexity_adjustment": adjustment,
            }
        )
        state = state.model_copy(update={"history": history})

        row = replay_entry(
            state, 1, workflows_dir=str(workflows), prompt_base=PROMPT_BASE
        )
        assert row.recorded_delta == 1
        assert row.base_tier == "medium"
        assert row.trigger == "evidence-drift"
        assert "evidence drift" in row.rationale

    def test_single_entry_selection(self, tmp_path):
        workflows = _linear_workflow(tmp_path)
        rows = replay_entries(
            _state(),
            workflows_dir=str(workflows),
            prompt_base=PROMPT_BASE,
            entries=[2],
        )
        assert len(rows) == 1
        assert rows[0].step == "implement"


class TestRendering:
    def test_table_has_one_row_per_entry(self, tmp_path):
        workflows = _linear_workflow(tmp_path)
        rows = replay_entries(
            _state(), workflows_dir=str(workflows), prompt_base=PROMPT_BASE
        )
        table = render_table(rows)
        lines = [line for line in table.splitlines() if line.strip()]
        assert "CONFIG_COMPLEXITY" in lines[0]
        assert len(lines) == 2 + len(rows)


class TestCli:
    def _write_state(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(_state().model_dump_json())
        return path

    def test_state_file_offline_table(self, tmp_path, capsys):
        state_path = self._write_state(tmp_path)
        workflows = _linear_workflow(tmp_path)
        code = main(
            [
                "--state",
                str(state_path),
                "--all-entries",
                "--workflows-dir",
                str(workflows),
                "--local-workflows-dir",
                "",
                "--prompt-base",
                PROMPT_BASE,
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "Replay: state file" in out
        assert "policy:" in out
        assert "CONFIG_COMPLEXITY" in out
        assert "implement" in out

    def test_json_output(self, tmp_path, capsys):
        state_path = self._write_state(tmp_path)
        workflows = _linear_workflow(tmp_path)
        code = main(
            [
                "--state",
                str(state_path),
                "--entry",
                "1",
                "--json",
                "--workflows-dir",
                str(workflows),
                "--local-workflows-dir",
                "",
                "--prompt-base",
                PROMPT_BASE,
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload[0]["entry"] == 1
        assert payload[0]["base_tier"] == "high"
        assert payload[0]["config_complexity"] == "medium"

    def test_entry_out_of_range(self, tmp_path, capsys):
        state_path = self._write_state(tmp_path)
        code = main(["--state", str(state_path), "--entry", "42"])
        assert code == 2
        assert "out of range" in capsys.readouterr().err

    def test_missing_state_file(self, tmp_path, capsys):
        code = main(["--state", str(tmp_path / "absent.json")])
        assert code == 2
        assert "cannot read" in capsys.readouterr().err

    def test_issue_without_repo(self, capsys, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        code = main(["--issue", "1299"])
        assert code == 2
        assert "--repo" in capsys.readouterr().err


class TestIssueLoading:
    def test_issue_state_comes_from_comments(self, monkeypatch):
        state = _state()
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + state.model_dump_json()
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        monkeypatch.setattr(
            "nebuko.engine.lib.github_client.get_issue",
            lambda *a, **k: {"body": "issue body"},
        )
        monkeypatch.setattr(
            "nebuko.engine.lib.github_client.get_issue_comments",
            lambda *a, **k: [{"body": body, "id": 1}],
        )
        loaded = replay.load_state_from_issue(1299, "o/r")
        assert loaded is not None
        assert loaded.workflow == "wf"
