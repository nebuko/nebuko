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

sys.path.insert(0, str(Path(__file__).parent))

from workflow_state.workflow_state import STATE_MARKER_END, STATE_MARKER_START  # noqa: E402
from resolve_config import resolve_config  # noqa: E402
from resolve_workflow import TASK_TO_PROMPT  # noqa: E402

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
        "trigger": {"agents": ["dev"]},
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
        "trigger": {"agents": ["genius"]},
        "steps": [
            {"name": "architect", "agent": "genius", "next": "context"},
            {"name": "context", "agent": "ctx", "next": "plan"},
            {
                "name": "plan",
                "agent": "genius",
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
        "trigger": {"agents": ["dev"]},
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
        "trigger": {"agents": ["dev"]},
        "steps": [
            {"name": "step_1", "agent": "dev", "skill": ["step-1"], "next": "step_2"},
            {"name": "step_2", "agent": "dev", "skill": ["step-2"], "next": None},
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
def scaffold_workflows_dir(tmp_path: Path) -> Path:
    """Materialize the issue-#648 scaffold shape: plan-orchestration's
    ``sub_impl`` mounts a branch file whose ``step_1`` is a runnable agent."""
    (tmp_path / "plan-orchestration.yaml").write_text(yaml.dump(_plan_orchestration()))
    (tmp_path / "tmp_impl").mkdir()
    (tmp_path / "tmp_impl" / "workflow.yaml").write_text(yaml.dump(_tmp_impl_scaffold()))
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
            "position": [
                {"workflow": "plan-orchestration", "step": "sub_impl"},
                {"workflow": "tmp-impl", "step": "step_2"},
                {"workflow": "dev-review-loop", "step": "implement"},
            ],
        },
        {
            "step": "review", "agent": "review", "summary": "",
            "position": [
                {"workflow": "plan-orchestration", "step": "sub_impl"},
                {"workflow": "tmp-impl", "step": "step_2"},
                {"workflow": "dev-review-loop", "step": "review"},
            ],
        },
    ]
    return state


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
        state["dispatch"] = dict(state["dispatch"], branch="rejected")
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