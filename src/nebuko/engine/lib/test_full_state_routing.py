"""Full-``workflow_state`` routing tests (issue #657).

Replays captured ``workflow_state`` JSON (verbatim, fixture files under
``fixtures/``) end-to-end through ``resolve_config()`` — marker embedding,
state parse, dispatch extraction, routing resolution — against a
materialized 3-level nested workflows dir:

    plan-orchestration -> tmp_impl/workflow.yaml -> dev-review-loop

``tmp_impl/workflow.yaml`` is branch-mounted in real runs and absent on
``main``; materializing it in a temp dir is what makes the multi-step plan
observable. Case 1 pins the *correct* routing for the captured issue-#657
state (review approved on plan step 1 → re-enter the plan at
``tmp-impl/step_2`` → ``dev-review-loop/implement``) instead of returning to
the outermost parent (``plan-orchestration/sub_clean``). A further case pins
the round-2 unwind: a terminal review at ``tmp-impl/step_2`` must resume the
outermost parent's successor (``plan-orchestration/sub_clean``) rather than
end the run.

Debug hook (quick replay of any captured state):

    python3 test_full_state_routing.py <state.json> [workflows_dir]
"""

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest
import yaml


from nebuko.engine.lib.workflow_state.workflow_state import STATE_MARKER_END, STATE_MARKER_START
from nebuko.engine.lib.resolve_config import resolve_config
from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# @stub-protected
@dataclass(frozen=True)
class RoutingCase:
    """One captured full ``workflow_state`` plus its expected resolved routing."""

    name: str
    state: dict
    workflows_dir: Path
    expected: dict

    # @stub-protected
    def trigger_body(self) -> str:
        """State JSON embedded between the WORKFLOW_STATE markers."""
        return (
            "<!-- bot-agent-review -->\n"
            + STATE_MARKER_START + "\n"
            + json.dumps(self.state)
            + "\n" + STATE_MARKER_END + "\n"
        )


# @stub-protected
def run_case(case: RoutingCase, prompt_base: str, is_bot: bool = True) -> dict:
    """Run one replay case through the full ``resolve_config()`` path."""
    return resolve_config(
        case.trigger_body(), prompt_base, str(case.workflows_dir), is_bot=is_bot,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _dev_review_loop() -> dict:
    """Loop semantics verbatim from workflows/dev-review-loop.yaml."""
    return {
        "name": "dev-review-loop",
        "description": "Execute a single plan step with review; loops on rejection",
        "steps": [
            {"name": "implement", "agent": "dev", "next": "review"},
            {
                "name": "review",
                "agent": "review",
                "conditions": [
                    {"on_result": "approved", "next": None},
                    {"on_result": "rejected", "next": "implement"},
                ],
            },
        ],
    }


def _plan_orchestration() -> dict:
    """Real content from workflows/plan-orchestration.yaml."""
    return {
        "name": "plan-orchestration",
        "description": "Full lifecycle — architect, context, plan, scaffold, execute, cleanup, final review",
        "steps": [
            {"name": "architect", "agent": "architect", "next": "context"},
            {"name": "context", "agent": "context", "next": "plan"},
            {
                "name": "plan",
                "agent": "architect",
                "conditions": [
                    {"on_result": "approved", "next": "scaffold"},
                    {"on_result": "rejected", "next": "plan"},
                ],
            },
            {"name": "scaffold", "agent": "dev", "skill": ["scaffold-plan"], "next": "sub_impl"},
            {"name": "sub_impl", "workflow": "tmp_impl/workflow.yaml", "next": "sub_clean"},
            {"name": "sub_clean", "agent": "dev", "skill": ["plan-cleanup"], "next": "final_review"},
            {
                "name": "final_review",
                "agent": "review",
                "conditions": [
                    {"on_result": "approved", "next": None},
                    {"on_result": "rejected", "next": "final_fix"},
                ],
            },
            {"name": "final_fix", "agent": "dev", "skill": ["final-fix"], "next": "final_review"},
        ],
    }


def _tmp_impl() -> dict:
    """The branch-mounted file the real checkout is missing (issue #657)."""
    return {
        "name": "tmp-impl",
        "description": "Dynamically generated per-step execution workflow",
        "steps": [
            {"name": "step_1", "workflow": "dev-review-loop", "next": "step_2"},
            {"name": "step_2", "workflow": "dev-review-loop", "next": None},
        ],
    }


def _tmp_impl_scaffold() -> dict:
    """The scaffolded per-step plan shape (issue #648): ``step_1`` is a
    runnable agent step, so the mount descent must stop at it."""
    return {
        "name": "tmp-impl",
        "description": "Dynamically generated per-step execution workflow",
        "steps": [
            {"name": "step_1", "agent": "dev", "skill": ["step-1"], "next": "step_2"},
            {"name": "step_2", "agent": "dev", "skill": ["step-2"], "next": None},
        ],
    }


def _tmp_impl_three() -> dict:
    """A three-step scaffolded plan (issue #822): each ``step_N`` mounts the
    ``dev-review-loop`` child, so a dotted selector can address ``step_3`` and
    the loop's ``review`` leaf below it."""
    return {
        "name": "tmp-impl",
        "description": "Dynamically generated per-step execution workflow",
        "steps": [
            {"name": "step_1", "workflow": "dev-review-loop", "next": "step_2"},
            {"name": "step_2", "workflow": "dev-review-loop", "next": "step_3"},
            {"name": "step_3", "workflow": "dev-review-loop", "next": None},
        ],
    }


# @stub-protected
@pytest.fixture
def nested_workflows_dir(tmp_path: Path) -> Path:
    """Materialize the full nested shape: plan-orchestration -> tmp_impl/workflow.yaml -> dev-review-loop."""
    (tmp_path / "dev-review-loop.yaml").write_text(yaml.dump(_dev_review_loop()))
    (tmp_path / "plan-orchestration.yaml").write_text(yaml.dump(_plan_orchestration()))
    (tmp_path / "tmp_impl").mkdir()
    (tmp_path / "tmp_impl" / "workflow.yaml").write_text(yaml.dump(_tmp_impl()))
    return tmp_path


@pytest.fixture
def nested_three_workflows_dir(tmp_path: Path) -> Path:
    """Nested shape whose scaffolded plan has three steps (issue #822)."""
    (tmp_path / "dev-review-loop.yaml").write_text(yaml.dump(_dev_review_loop()))
    (tmp_path / "plan-orchestration.yaml").write_text(yaml.dump(_plan_orchestration()))
    (tmp_path / "tmp_impl").mkdir()
    (tmp_path / "tmp_impl" / "workflow.yaml").write_text(yaml.dump(_tmp_impl_three()))
    return tmp_path


@pytest.fixture
def scaffold_workflows_dir(tmp_path: Path) -> Path:
    """Materialize the issue-#648 scaffold shape: plan-orchestration's
    ``sub_impl`` mounts a branch file whose ``step_1`` is a runnable agent."""
    (tmp_path / "plan-orchestration.yaml").write_text(yaml.dump(_plan_orchestration()))
    (tmp_path / "tmp_impl").mkdir()
    (tmp_path / "tmp_impl" / "workflow.yaml").write_text(yaml.dump(_tmp_impl_scaffold()))
    return tmp_path


@pytest.fixture
def unscaffolded_workflows_dir(tmp_path: Path) -> Path:
    """plan-orchestration alone — ``tmp_impl/workflow.yaml`` was never scaffolded."""
    (tmp_path / "dev-review-loop.yaml").write_text(yaml.dump(_dev_review_loop()))
    (tmp_path / "plan-orchestration.yaml").write_text(yaml.dump(_plan_orchestration()))
    return tmp_path


@pytest.fixture
def prompt_base(tmp_path: Path) -> str:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in TASK_TO_PROMPT.values():
        (prompts / name).write_text(f"# Prompt for {name}")
    return str(prompts)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

ISSUE657_REVIEW_APPROVED = "workflow_state_issue657_step1_review_approved"
ISSUE648_SCAFFOLD = "workflow_state_issue648_scaffold"


def _issue657_case(name: str, state: dict, workflows_dir: Path,
                   expected: dict) -> RoutingCase:
    return RoutingCase(name=name, state=state, workflows_dir=workflows_dir,
                       expected=expected)


def _step2_implement_expected() -> dict:
    """Correct routing: re-enter the plan at tmp-impl/step_2 → dev-review-loop/implement."""
    return {
        "TASK": "dev",
        "WORKFLOW_NAME": "dev-review-loop",
        "WORKFLOW_STEP": "implement",
        "WORKFLOW_POSITION_PATH": "tmp-impl/step_2|dev-review-loop/implement",
    }


def _round2_state() -> dict:
    """State after the plan was re-entered at ``tmp-impl/step_2``.

    The recorded parent pair is now the intermediate level; the position
    stacks carry the full ``plan-orchestration -> tmp-impl -> dev-review-loop``
    nesting, so the terminal review must unwind one level outward to the
    outermost parent's successor (``plan-orchestration/sub_clean``).
    """
    state = _load_fixture(ISSUE657_REVIEW_APPROVED)
    state["parent_workflow"] = "tmp-impl"
    state["parent_workflow_step"] = "step_2"
    state["position"] = [
        {"workflow": "plan-orchestration", "step": "sub_impl"},
        {"workflow": "tmp-impl", "step": "step_2"},
        {"workflow": "dev-review-loop", "step": "review"},
    ]
    state["history"] = [
        {
            "step": "implement", "agent": "dev", "summary": "",
            "turn_outcome": {"status": "completed", "appreciation": "approved"},
            "position": [
                {"workflow": "plan-orchestration", "step": "sub_impl"},
                {"workflow": "tmp-impl", "step": "step_2"},
                {"workflow": "dev-review-loop", "step": "implement"},
            ],
        },
        {
            "step": "review", "agent": "review", "summary": "",
            "turn_outcome": {"status": "completed", "appreciation": "approved"},
            "position": [
                {"workflow": "plan-orchestration", "step": "sub_impl"},
                {"workflow": "tmp-impl", "step": "step_2"},
                {"workflow": "dev-review-loop", "step": "review"},
            ],
        },
    ]
    return state


def _default_workflow() -> dict:
    """Verbatim ``defaults/workflows/default.yaml`` (``plan`` is a human_gate)."""
    return {
        "name": "default",
        "description": "Standard development workflow: analyze → context → plan → implement → review",
        "trigger": {"agents": ["architect"]},
        "steps": [
            {"name": "analyze", "agent": "architect", "complexity": "low", "next": "context"},
            {"name": "context", "agent": "context", "next": "plan"},
            {
                "name": "plan",
                "agent": "architect",
                "next": "implement",
                "human_gate": True,
                "conditions": [
                    {"on_result": "approved", "next": "implement"},
                    {"on_result": "rejected", "next": "context"},
                ],
            },
            {"name": "implement", "agent": "dev", "next": "review"},
            {
                "name": "review",
                "agent": "review",
                "conditions": [
                    {"on_result": "approved", "next": None},
                    {"on_result": "rejected", "next": "implement"},
                ],
            },
        ],
    }


@dataclass(frozen=True)
class GateHoldCase:
    """A captured gate-hold state plus the trigger body shape to replay it with."""

    name: str
    state: dict
    workflows_dir: Path
    body_prefix: str = "<!-- bot-agent-architect -->\n"

    def trigger_body(self) -> str:
        return (
            self.body_prefix
            + STATE_MARKER_START + "\n"
            + json.dumps(self.state)
            + "\n" + STATE_MARKER_END + "\n"
        )


@pytest.fixture
def default_workflows_dir(tmp_path: Path) -> Path:
    """Materialize the shipped ``default`` workflow (its ``plan`` is gated)."""
    (tmp_path / "default.yaml").write_text(yaml.dump(_default_workflow()))
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFullStateRouting:
    """Replay captured full workflow_state JSONs through resolve_config()."""

    def test_issue657_step1_review_approved(
        self, nested_workflows_dir: Path, prompt_base: str,
    ):
        """Verbatim captured state (review approved on plan step 1) must re-enter the plan at step_2."""
        case = _issue657_case(
            name=ISSUE657_REVIEW_APPROVED,
            state=_load_fixture(ISSUE657_REVIEW_APPROVED),
            workflows_dir=nested_workflows_dir,
            expected=_step2_implement_expected(),
        )
        result = run_case(case, prompt_base)
        for key, want in case.expected.items():
            got = result.get(key, "")
            # Path-like keys are matched as substring (the exact leading
            # prefix depends on how the position chain is emitted).
            assert want in got, f"{key}: expected {want!r} in {got!r}"

    def test_issue657_step1_review_rejected(
        self, nested_workflows_dir: Path, prompt_base: str,
    ):
        """Rejected review must loop to dev-review-loop/implement with no return-to-parent."""
        state = _load_fixture(ISSUE657_REVIEW_APPROVED)
        state["turn_outcome"] = dict(
            state["turn_outcome"], appreciation="rejected",
        )
        case = _issue657_case(
            name=ISSUE657_REVIEW_APPROVED + "_rejected",
            state=state,
            workflows_dir=nested_workflows_dir,
            expected=_step2_implement_expected(),
        )
        result = run_case(case, prompt_base)
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == "dev-review-loop"
        assert result["WORKFLOW_STEP"] == "implement"
        assert "sub_clean" not in result.get("WORKFLOW_POSITION_PATH", "")

    def test_issue657_step2_review_approved_unwinds_to_cleanup(
        self, nested_workflows_dir: Path, prompt_base: str,
    ):
        """Terminal review at tmp-impl/step_2 must unwind to plan-orchestration/sub_clean."""
        case = _issue657_case(
            name=ISSUE657_REVIEW_APPROVED + "_step2",
            state=_round2_state(),
            workflows_dir=nested_workflows_dir,
            expected={},
        )
        result = run_case(case, prompt_base)
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == "plan-orchestration"
        assert result["WORKFLOW_STEP"] == "sub_clean"
        assert result["STEP_SKILLS"] == "plan-cleanup"
        assert result.get("WORKFLOW_POSITION_PATH", "").endswith(
            "plan-orchestration/sub_clean"
        )

    def test_issue648_scaffold_descends_to_step1(
        self, scaffold_workflows_dir: Path, prompt_base: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Issue #648: a completed ``scaffold`` step must descend into the
        branch-mounted ``sub_impl`` and start its ``step_1``.

        Before the fix the trigger-side resolver returned ``{"task": None,
        "step": "sub_impl"}`` because ``sub_impl`` is a mount (no agent), so
        ``build_result`` emitted ``REASON="No valid task found"`` and the
        workflow stopped instead of running ``step_1``.

        ``tmp_impl/workflow.yaml`` is a path-like reference resolved relative
        to the repository root, so the materialized workflows dir stands in
        for the checkout root.
        """
        monkeypatch.chdir(scaffold_workflows_dir)
        case = _issue657_case(
            name=ISSUE648_SCAFFOLD,
            state=_load_fixture(ISSUE648_SCAFFOLD),
            workflows_dir=scaffold_workflows_dir,
            expected={},
        )
        result = run_case(case, prompt_base)
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == "tmp-impl"
        assert result["WORKFLOW_STEP"] == "step_1"
        assert result["SKIP_AGENT"] == "false"
        assert result.get("REASON") != "No valid task found"
        assert result.get("WORKFLOW_POSITION_PATH", "").endswith(
            "plan-orchestration/sub_impl|tmp-impl/step_1"
        )


# ---------------------------------------------------------------------------
# Human-gate hold precedence (issue #885 / #887)
# ---------------------------------------------------------------------------

ISSUE885_GATE_HOLD = "workflow_state_issue885_gate_hold"
ISSUE923_REVIEW_ROUND2 = "workflow_state_issue923_review_round2"


class TestHumanGateHoldPrecedence:
    """A persisted ``human_gate`` hold must block auto-advance (issues #885, #887).

    Replays the captured issue-#885 state verbatim: the architect's ``plan``
    step emitted ``dispatch.status == "hold"`` with ``reason ==
    "human_gate:plan"``, yet the bot re-trigger advanced to ``implement``
    because the workflow forward edge (``plan -> implement``) resolved first.
    """

    def _case(self, workflows_dir: Path, body_prefix: str) -> GateHoldCase:
        return GateHoldCase(
            name=ISSUE885_GATE_HOLD,
            state=_load_fixture(ISSUE885_GATE_HOLD),
            workflows_dir=workflows_dir,
            body_prefix=body_prefix,
        )

    def test_bot_state_only_rerun_stays_held(
        self, default_workflows_dir: Path, prompt_base: str,
    ):
        """A bot re-trigger of the held state must SKIP, not advance to implement."""
        case = self._case(default_workflows_dir, "<!-- bot-agent-architect -->\n")
        result = resolve_config(
            case.trigger_body(), prompt_base, str(case.workflows_dir), is_bot=True,
        )
        assert result["SKIP_AGENT"] == "true"
        assert "human_gate" in result.get("REASON", "")
        assert result.get("TASK", "") != "dev"
        assert result.get("WORKFLOW_STEP", "") != "implement"

    def test_engine_sentinel_variant_also_held(
        self, default_workflows_dir: Path, prompt_base: str,
    ):
        """The AGENT_RUN_CONFIG-sentinel shape must behave identically."""
        case = self._case(
            default_workflows_dir,
            "<!-- bot-agent-architect -->\n<!-- AGENT_RUN_CONFIG "
            '{"dispatch": {"status": "hold", "reason": "human_gate:plan"}} -->\n',
        )
        result = resolve_config(
            case.trigger_body(), prompt_base, str(case.workflows_dir), is_bot=True,
        )
        assert result["SKIP_AGENT"] == "true"
        assert "human_gate" in result.get("REASON", "")
        assert result.get("TASK", "") != "dev"

    def test_gate_blocks_the_advance_but_lets_the_handoff_route(
        self, default_workflows_dir: Path, prompt_base: str,
    ):
        """Issue #1003/#1033: the gate blocks the workflow *advance*, never a
        delegation — a hand-off from an armed gate consults the callee and the
        gated step stays put, so the run never sneaks past to ``implement``."""
        state = _load_fixture(ISSUE885_GATE_HOLD)
        state["gate"] = {"step": "plan", "status": "armed"}
        state["function_call"] = {"function": "call_agent", "agent_name": "dev"}
        case = GateHoldCase(
            name=ISSUE885_GATE_HOLD + "_with_function_call",
            state=state,
            workflows_dir=default_workflows_dir,
        )
        result = resolve_config(
            case.trigger_body(), prompt_base, str(default_workflows_dir), is_bot=True,
        )
        assert result["SKIP_AGENT"] != "true"
        assert result.get("TASK") == "dev"
        # The callee runs at its own step; the gated step itself is not
        # advanced past (the gate stays armed for the next human action).
        assert result.get("WORKFLOW_NAME") == "default"

    def test_plain_hold_parks_without_a_gate_reason(
        self, default_workflows_dir: Path, prompt_base: str,
    ):
        """A plain (awaiting-input) hold parks the step without claiming a gate.

        The human_gate guard is scoped to ``hold_kind == awaiting_approval``
        exactly as the plan approved, so a plain hold must not read as one.
        """
        state = _load_fixture(ISSUE885_GATE_HOLD)
        plain_hold = {
            "status": "completed", "appreciation": "hold",
            "hold_kind": "awaiting_input", "note": "no-action default",
        }
        state["turn_outcome"] = dict(plain_hold)
        for entry in state["history"]:
            if entry.get("turn_outcome", {}).get("note", "").startswith("human_gate"):
                entry["turn_outcome"] = dict(plain_hold)
        case = GateHoldCase(
            name=ISSUE885_GATE_HOLD + "_plain_hold",
            state=state,
            workflows_dir=default_workflows_dir,
        )
        result = resolve_config(
            case.trigger_body(), prompt_base, str(default_workflows_dir), is_bot=True,
        )
        assert result["SKIP_AGENT"] == "true"
        assert result.get("TASK", "") != "dev"
        assert "human_gate" not in result.get("REASON", "")
        assert "Awaiting input" in result.get("REASON", "")


# ---------------------------------------------------------------------------
# Nested step addressing (issue #822)
# ---------------------------------------------------------------------------

def _run_workflow_body(step_selector: str) -> str:
    """A human comment whose first line selects a nested plan-orchestration step."""
    return (
        f"/nebuko run workflow --name plan-orchestration --step {step_selector}\n"
    )


def _run_selector(selector: str, workflows_dir: Path, prompt_base: str) -> dict:
    """Resolve ``/nebuko run workflow --step <selector>`` through resolve_config."""
    return resolve_config(
        _run_workflow_body(selector), prompt_base, str(workflows_dir), is_bot=False,
    )


class TestNestedStepPathRouting:
    """``--step sub_impl.step_3[.review]`` descends mounted sub-workflows (issue #822)."""

    def test_plain_mount_step_descends_to_first_step(
        self, nested_three_workflows_dir: Path, prompt_base: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A single-segment mount selector keeps the legacy first-step descent."""
        monkeypatch.chdir(nested_three_workflows_dir)
        result = _run_selector("sub_impl", nested_three_workflows_dir, prompt_base)
        assert result["TASK"] == "dev"
        assert result["SKIP_AGENT"] == "false"
        assert result["WORKFLOW_POSITION_PATH"].endswith(
            "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/implement"
        )

    def test_nested_step_addresses_third_scaffold_step(
        self, nested_three_workflows_dir: Path, prompt_base: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Issue #648 restart: ``sub_impl.step_3`` seeds plan step_3, skipping 1-2."""
        monkeypatch.chdir(nested_three_workflows_dir)
        result = _run_selector(
            "sub_impl.step_3", nested_three_workflows_dir, prompt_base,
        )
        assert result["TASK"] == "dev"
        assert result["SKIP_AGENT"] == "false"
        assert result["WORKFLOW_NAME"] == "dev-review-loop"
        assert result["WORKFLOW_STEP"] == "implement"
        assert result["WORKFLOW_POSITION_PATH"].endswith(
            "plan-orchestration/sub_impl|tmp-impl/step_3|dev-review-loop/implement"
        )

    def test_three_level_selector_addresses_review_leaf(
        self, nested_three_workflows_dir: Path, prompt_base: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """``sub_impl.step_3.review`` re-enters at just step_3's review."""
        monkeypatch.chdir(nested_three_workflows_dir)
        result = _run_selector(
            "sub_impl.step_3.review", nested_three_workflows_dir, prompt_base,
        )
        assert result["TASK"] == "review"
        assert result["SKIP_AGENT"] == "false"
        assert result["WORKFLOW_NAME"] == "dev-review-loop"
        assert result["WORKFLOW_STEP"] == "review"
        assert result["WORKFLOW_POSITION_PATH"].endswith(
            "plan-orchestration/sub_impl|tmp-impl/step_3|dev-review-loop/review"
        )

    def test_unknown_nested_segment_fails_loudly(
        self, nested_three_workflows_dir: Path, prompt_base: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """An absent nested segment must fail, not silently fall back to step_1."""
        monkeypatch.chdir(nested_three_workflows_dir)
        result = _run_selector(
            "sub_impl.step_9", nested_three_workflows_dir, prompt_base,
        )
        assert result["SKIP_AGENT"] == "true"
        assert "step_9" in result.get("REASON", "")
        assert not result.get("WORKFLOW_POSITION_PATH")

    def test_malformed_selector_fails_loudly(
        self, nested_three_workflows_dir: Path, prompt_base: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Empty segments (double/leading/trailing dot) are rejected."""
        monkeypatch.chdir(nested_three_workflows_dir)
        for selector in ("sub_impl..step_3", "sub_impl.step_3.", ".step_3"):
            result = _run_selector(
                selector, nested_three_workflows_dir, prompt_base,
            )
            assert result["SKIP_AGENT"] == "true", selector
            assert "invalid step name" in result.get("REASON", ""), selector

    def test_unscaffolded_mount_fails_loudly(
        self, unscaffolded_workflows_dir: Path, prompt_base: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A nested selector whose child was never scaffolded fails loudly."""
        monkeypatch.chdir(unscaffolded_workflows_dir)
        result = _run_selector(
            "sub_impl.step_3", unscaffolded_workflows_dir, prompt_base,
        )
        assert result["SKIP_AGENT"] == "true"
        assert "tmp_impl/workflow.yaml" in result.get("REASON", "")
        assert not result.get("WORKFLOW_POSITION_PATH")


# ---------------------------------------------------------------------------
# Issue #924: a verdict-less review must never look like a finished workflow
# ---------------------------------------------------------------------------


class TestReviewRound2Restart:
    """Replay the captured issue-#923 round-2 state (issue #924).

    The review agent was invoked, rejected the implementation and asked for a
    second round — but it signalled that only through free text (the recorded
    outcome was INCOMPLETE/``no_outcome``, no verdict). The workflow's
    ``review`` step declares ``on_result`` edges, so nothing matched: the
    routing is *unresolved*, not a declared finish. Before the fix the engine
    collapsed both to ``None`` and pinged the human "*workflow default has
    finished at step review*" instead of re-running ``dev``.
    """

    def _replay(self, default_workflows_dir: Path, prompt_base: str, **outcome):
        state = _load_fixture(ISSUE923_REVIEW_ROUND2)
        if outcome:
            state["turn_outcome"] = outcome
        case = RoutingCase(
            name=ISSUE923_REVIEW_ROUND2,
            state=state,
            workflows_dir=default_workflows_dir,
            expected={},
        )
        return run_case(case, prompt_base)

    def test_verdictless_capture_is_unresolved_not_a_finish(
        self, default_workflows_dir: Path, prompt_base: str,
    ):
        """The verbatim captured blob still dead-ends the resolver.

        It must resolve as the *unresolved* kind, which is what the engine's
        notice layer now reads as "needs input" instead of "workflow complete".
        """
        result = self._replay(default_workflows_dir, prompt_base)
        assert result["SKIP_AGENT"] == "true"
        # Not a terminal finish: the engine's notice layer reads SKIP_KIND
        # "terminal" as workflow-complete, so this must not carry it.
        assert result.get("SKIP_KIND") != "terminal"
        # #978: the verdictless turn parks on the missing outcome rather than a
        # missing successor; either way it is an unresolved skip, not a finish.
        assert "no verdict" in result.get("REASON", "")

        from nebuko.engine.lib.resolve_workflow import RouteKind, resolve_next_from_workflow

        resolution = resolve_next_from_workflow(
            "default", "review", str(default_workflows_dir),
        )
        assert resolution is not None
        assert resolution.kind is RouteKind.UNRESOLVED

    def test_rejected_round_2_restarts_the_dev_loop(
        self, default_workflows_dir: Path, prompt_base: str,
    ):
        """With the verdict the review prompt now requires, ``dev`` restarts."""
        result = self._replay(
            default_workflows_dir, prompt_base,
            status="completed", appreciation="rejected", round=2,
        )
        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == "default"
        assert result["WORKFLOW_STEP"] == "implement"


# ---------------------------------------------------------------------------
# Debug hook: replay any captured state JSON
# ---------------------------------------------------------------------------

def _materialize_default_workflows_dir() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="full_state_routing_"))
    (tmp / "dev-review-loop.yaml").write_text(yaml.dump(_dev_review_loop()))
    (tmp / "plan-orchestration.yaml").write_text(yaml.dump(_plan_orchestration()))
    (tmp / "tmp_impl").mkdir()
    (tmp / "tmp_impl" / "workflow.yaml").write_text(yaml.dump(_tmp_impl()))
    return str(tmp)


def _default_prompt_base() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="full_state_prompts_"))
    for name in TASK_TO_PROMPT.values():
        (tmp / name).write_text(f"# Prompt for {name}")
    return str(tmp)


def _replay(state_file: str, workflows_dir: Optional[str]) -> int:
    state = json.loads(Path(state_file).read_text())
    wf_dir = workflows_dir or _materialize_default_workflows_dir()
    case = RoutingCase(
        name=Path(state_file).stem, state=state, workflows_dir=Path(wf_dir),
        expected={},
    )
    result = run_case(case, _default_prompt_base())
    for key in sorted(result):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("Usage: python3 test_full_state_routing.py <state.json> [workflows_dir]")
        sys.exit(1)
    sys.exit(_replay(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))