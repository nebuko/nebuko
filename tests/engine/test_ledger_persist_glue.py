"""Engine-glue regression tests for the requirements ledger (#1287).

``_requirements_body()`` reads the analyze step's output from run state and
must hand the renderer an ``OutputContract`` — not the raw ``StepOutput``
model that ``_declared_outputs()`` returns. Nothing exercised ``persist()``
on a workflow whose analyze step declares ``requirements``, so the
AttributeError shipped and every post-analyze run crashed before its commits
were pushed.
"""

from __future__ import annotations

import pytest

from nebuko.engine.run_loop import Engine
from nebuko.engine.finalize import SECTION_CLOSE, SECTION_OPEN


def _ledger_section() -> str:
    return "\n".join(
        [
            "| ID | Kind | Statement | Source | Origin |",
            "| --- | --- | --- | --- | --- |",
            '| R1 | requirement | The engine persists ledger metrics | "R1" | explicit |',
        ]
    )


def _coverage_body(covered: str = "R1") -> str:
    return "\n".join(
        [
            "| ID | Covered by | Verified by |",
            "| --- | --- | --- |",
            f"| {covered} | the persist path | this test |",
        ]
    )


def _engine(tmp_path):
    from nebuko.adapters.local import LocalAdapter
    from nebuko.engine.models import Trigger
    from nebuko.engine.paths import repo_root

    trigger = Trigger(
        author="olivier-norture",
        body="@@dev fix it",
        type="issue_comment",
        number=5,
        issue_body="",
    )
    adapter = LocalAdapter(tmp_path / "runs" / "r1", trigger=trigger)
    return Engine(
        adapter=adapter,
        repo_root=repo_root(),
        env={"GITHUB_REPOSITORY": "olivier-norture/nebuko"},
    )


def _seed_analyze_output(engine: Engine, requirements_body: str) -> None:
    """Give the run state an analyze output carrying a requirements section."""
    from nebuko.engine.lib.workflow_state import to_git_comment
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState
    from nebuko.engine.models import CommentEntry

    state = WorkflowState(workflow="default", step="analyze", agent="architect")
    body = "\n".join(
        [
            "# Analysis",
            SECTION_OPEN.format(name="requirements"),
            requirements_body,
            SECTION_CLOSE,
            to_git_comment(state),
        ]
    )
    engine.adapter.post(body)
    engine._context_state["history"] = [
        CommentEntry(
            author="bot",
            body=body,
            created_at="2026-11-03T00:00:00Z",
            comment_id="1",
        )
    ]


def _declare_default_workflow(monkeypatch) -> None:
    """Point ``_declared_outputs`` at the real shipped default workflow."""
    from nebuko.engine.lib.config import load_workflow

    definition = load_workflow("defaults/workflows/default.yaml")
    declared = {s.name: s.output for s in definition.steps if s.output is not None}
    assert "requirements" in [f.name for f in declared["analyze"].fields]
    monkeypatch.setattr(Engine, "_declared_outputs", lambda self: declared)



def test_persist_records_ledger_metrics_after_analyze(tmp_path, monkeypatch):
    """R2: a step after analyze gets through persist() and records metrics."""
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

    engine = _engine(tmp_path)
    _seed_analyze_output(engine, _ledger_section())
    _declare_default_workflow(monkeypatch)
    state = WorkflowState(workflow="default", step="context", agent="context")
    monkeypatch.setattr(Engine, "_previous_state", lambda self: state)

    persisted = engine.persist({"answer": "dev answer"})

    assert persisted is not None
    assert persisted.current_context["ledger_metrics"]["ledger_size"] == 1


def test_persist_survives_an_unparseable_ledger(tmp_path, monkeypatch):
    """K1: an unparseable ledger leaves the state untouched, never raises."""
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

    engine = _engine(tmp_path)
    _seed_analyze_output(engine, "not a ledger table at all")
    _declare_default_workflow(monkeypatch)
    prev = WorkflowState(workflow="default", step="context", agent="context")
    monkeypatch.setattr(Engine, "_previous_state", lambda self: prev)

    persisted = engine.persist({"answer": "dev answer"})

    assert persisted is not None
    assert persisted.current_context["ledger_metrics"]["ledger_size"] == 0


def test_persist_survives_a_broken_metric_collector(tmp_path, monkeypatch, caplog):
    """K1: an unexpected error in metric collection is logged, never raised."""
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState
    from nebuko.engine.lib.agent_script.sub_domains.metrics import ledger_metrics

    engine = _engine(tmp_path)
    _seed_analyze_output(engine, _ledger_section())
    _declare_default_workflow(monkeypatch)
    prev = WorkflowState(workflow="default", step="context", agent="context")
    monkeypatch.setattr(Engine, "_previous_state", lambda self: prev)

    def boom(self, ledger, coverage_refusals):
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(ledger_metrics.LedgerMetricsCollector, "collect", boom)

    persisted = engine.persist({"answer": "dev answer"})

    assert persisted is not None  # persist() completed instead of crashing
    assert any(
        "ledger metric collection failed" in record.getMessage()
        for record in caplog.records
    )


def test_requirements_body_reads_ledger_from_run_state(tmp_path, monkeypatch):
    """R3: the coverage guard consumes the ledger read from run state."""
    from nebuko.engine.ledger import PlanCoverageGuard

    engine = _engine(tmp_path)
    _seed_analyze_output(engine, _ledger_section())
    _declare_default_workflow(monkeypatch)

    assert engine._requirements_body() == _ledger_section()
    PlanCoverageGuard().check(_coverage_body(), engine._requirements_body())


def test_requirements_body_refuses_uncovered_id(tmp_path, monkeypatch):
    from nebuko.engine.ledger import CoverageValidationError, PlanCoverageGuard

    engine = _engine(tmp_path)
    _seed_analyze_output(engine, _ledger_section())
    _declare_default_workflow(monkeypatch)

    with pytest.raises(CoverageValidationError):
        PlanCoverageGuard().check(_coverage_body("K9"), engine._requirements_body())
