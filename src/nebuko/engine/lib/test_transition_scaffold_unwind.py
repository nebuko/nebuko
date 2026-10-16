"""Exit-path scaffold fetch-on-miss reproduction tests (issue #675).

Task #617 capture: after ``dev-review-loop/review`` approved on plan step 1,
the ``plan-orchestration`` ``sub_impl`` mount unwound straight to
``plan-orchestration/sub_clean`` (skill ``plan-cleanup``) instead of advancing
the plan to ``tmp-impl/step_2``. The intermediate ``tmp-impl`` level was
dropped because the exit-path loaders (``_load_workflow_by_name`` /
``_load_workflow_by_reference``) scan only the default-branch checkout, while
the branch-mounted ``tmp_impl/workflow.yaml`` exists only on
``scaffold_branch``. The entry path already fetches on miss
(``_load_child_workflow``); the exit path must apply the same policy.

These tests materialize only the default-branch workflows (no local
``tmp_impl/``) and monkeypatch the scaffold fetch, so the branch-only file is
observable exactly as in a real run. Before the fix the primary case resolves
to ``sub_clean``; after it resolves to ``dev-review-loop/implement`` at
``tmp-impl/step_2``.

The second case is a regression guard for the observed #617 cascade: a
terminal plan-level ``final_review`` must end the run, not re-enter the
cleanup or scaffold steps.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

import resolve_workflow  # noqa: E402
from resolve_config import resolve_config  # noqa: E402
from resolve_workflow import TASK_TO_PROMPT  # noqa: E402
from workflow_state.workflow_state import (  # noqa: E402
    STATE_MARKER_END,
    STATE_MARKER_START,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ISSUE657_REVIEW_APPROVED = "workflow_state_issue657_step1_review_approved"

# Scaffold branch the captured #617 state recorded (the branch-only mount lives
# there). The on-disk fixture predates the field, so the test injects it.
SCAFFOLD_BRANCH = "feature/issue-617-history-summary-pinned"

# Refs the exit-path loader may use to locate the intermediate mount. The
# branch-mounted file declares ``name: tmp-impl`` while living at
# ``tmp_impl/workflow.yaml``, so both the path form and the declared name are
# accepted.
_TMP_IMPL_REFS = {
    "tmp_impl/workflow.yaml",
    "tmp_impl/workflow.yml",
    "tmp-impl",
    "tmp-impl.yaml",
}


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
    """The branch-mounted per-step plan file (absent from the checkout)."""
    return {
        "name": "tmp-impl",
        "description": "Dynamically generated per-step execution workflow",
        "steps": [
            {"name": "step_1", "workflow": "dev-review-loop", "next": "step_2"},
            {"name": "step_2", "workflow": "dev-review-loop", "next": None},
        ],
    }


def _trigger_body(state: dict) -> str:
    """State JSON embedded between the WORKFLOW_STATE markers."""
    return (
        "<!-- bot-agent-review -->\n"
        + STATE_MARKER_START + "\n"
        + json.dumps(state)
        + "\n" + STATE_MARKER_END + "\n"
    )


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


@pytest.fixture
def branch_only_workflows_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Materialize only the default-branch workflows; ``tmp_impl/`` is branch-only.

    The scaffold fetch is monkeypatched to serve the branch-mounted
    ``tmp-impl`` shape, mirroring the real fetch-on-miss contract
    (``_scaffold_fetch`` returns an absolute path, or ``None`` on a miss).
    The branch file is written *outside* the workflows dir so the exit-path
    loader's recursive local scan cannot accidentally find it — otherwise the
    local-scan success would mask the missing fetch.
    """
    workflows_dir = tmp_path_factory.mktemp("default-branch-workflows")
    (workflows_dir / "plan-orchestration.yaml").write_text(yaml.dump(_plan_orchestration()))
    (workflows_dir / "dev-review-loop.yaml").write_text(yaml.dump(_dev_review_loop()))

    branch_file = tmp_path_factory.mktemp("scaffold-branch") / "workflow.yaml"
    branch_file.write_text(yaml.dump(_tmp_impl()))

    def fake_scaffold_fetch(ref: str, scaffold_branch: str) -> "str | None":
        return str(branch_file) if ref in _TMP_IMPL_REFS else None

    monkeypatch.setattr(resolve_workflow, "_scaffold_fetch", fake_scaffold_fetch)
    return workflows_dir


@pytest.fixture
def prompt_base(tmp_path_factory: pytest.TempPathFactory) -> str:
    prompts = tmp_path_factory.mktemp("prompts")
    for name in TASK_TO_PROMPT.values():
        (prompts / name).write_text(f"# Prompt for {name}")
    return str(prompts)


def _final_review_approved_state() -> dict:
    """The #617 v10 state: the plan-level ``final_review`` approved.

    ``final_review`` is an outermost ``plan-orchestration`` step, so no parent
    pair applies; ``approved`` is terminal (``next: null``). The captured run
    re-entered ``plan-orchestration`` afterwards (the observed cycle), so this
    pins termination instead.
    """
    return {
        "version": 10,
        "workflow": "plan-orchestration",
        "step": "final_review",
        "agent": "review",
        "phase": "review",
        "history": [
            {
                "step": "sub_clean",
                "agent": "dev",
                "summary": "",
                "position": [{"workflow": "plan-orchestration", "step": "sub_clean"}],
            },
            {
                "step": "final_review",
                "agent": "review",
                "summary": "",
                "position": [{"workflow": "plan-orchestration", "step": "final_review"}],
            },
        ],
        "parent_workflow": "",
        "parent_workflow_step": "",
        "scaffold_branch": SCAFFOLD_BRANCH,
        "turn_outcome": {"status": "completed", "appreciation": "approved"},
        "resolution": {
            "selected_workflow": "plan-orchestration",
            "selected_step": "final_review",
        },
        "position": [{"workflow": "plan-orchestration", "step": "final_review"}],
    }


class TestScaffoldUnwind:
    """Exit of a branch-mounted nested mount must fetch-on-miss (issue #675)."""

    def test_review_approved_unwinds_to_step2_via_scaffold_fetch(
        self, branch_only_workflows_dir: Path, prompt_base: str,
    ) -> None:
        """Verbatim #617 v8 state must re-enter the plan at tmp-impl/step_2.

        Before the fix the intermediate ``tmp-impl`` mount cannot be loaded,
        so the unwind falls through to the outer mount's ``next``
        (``plan-orchestration/sub_clean`` / ``plan-cleanup``).
        """
        state = _load_fixture(ISSUE657_REVIEW_APPROVED)
        state["scaffold_branch"] = SCAFFOLD_BRANCH
        result = resolve_config(
            _trigger_body(state), prompt_base, str(branch_only_workflows_dir), is_bot=True,
        )
        assert result["WORKFLOW_NAME"] == "dev-review-loop"
        assert result["WORKFLOW_STEP"] == "implement"
        assert "tmp-impl/step_2" in result.get("WORKFLOW_POSITION_PATH", "")
        # Negative: the wrong hop to the clean step must not happen.
        assert result.get("WORKFLOW_STEP") != "sub_clean"
        assert "plan-cleanup" not in result.get("STEP_SKILLS", "")

    def test_final_review_approved_ends_instead_of_reentering(
        self, branch_only_workflows_dir: Path, prompt_base: str,
    ) -> None:
        """A terminal plan-level final_review must end, not route to cleanup/scaffold."""
        state = _final_review_approved_state()
        result = resolve_config(
            _trigger_body(state), prompt_base, str(branch_only_workflows_dir), is_bot=True,
        )
        assert result.get("WORKFLOW_STEP") != "sub_clean"
        assert result.get("WORKFLOW_STEP") != "scaffold"
        assert result.get("WORKFLOW_NEXT", "") == ""


# The one reference under which the branch mount is discoverable: the filename
# does not match the declared ``name: tmp-impl``, so a by-name fetch cannot
# find it (real ``_find_workflow_file`` semantics).
_DECLARED_MOUNT_REF = "tmp_impl/workflow.yaml"


@pytest.fixture
def strict_branch_only_workflows_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Like ``branch_only_workflows_dir`` but the fetch serves the mount *only*
    under its declared reference (``tmp_impl/workflow.yaml``).

    Reproduces the real exit-path miss of issue #812: ``_find_workflow_file``
    cannot resolve ``tmp-impl``/``tmp-impl.yaml`` on the branch, so the exit
    path must ask for the reference the enclosing mount step declares.
    """
    workflows_dir = tmp_path_factory.mktemp("default-branch-workflows")
    (workflows_dir / "plan-orchestration.yaml").write_text(yaml.dump(_plan_orchestration()))
    (workflows_dir / "dev-review-loop.yaml").write_text(yaml.dump(_dev_review_loop()))

    branch_file = tmp_path_factory.mktemp("scaffold-branch") / "workflow.yaml"
    branch_file.write_text(yaml.dump(_tmp_impl()))

    def fake_scaffold_fetch(ref: str, scaffold_branch: str) -> "str | None":
        return str(branch_file) if ref == _DECLARED_MOUNT_REF else None

    monkeypatch.setattr(resolve_workflow, "_scaffold_fetch", fake_scaffold_fetch)
    return workflows_dir


def _step1_review_approved_full_stack_state() -> dict:
    """Post-#812 state: a terminal ``dev-review-loop/review`` approval on plan
    step 1, with the full ``plan-orchestration -> tmp-impl -> dev-review-loop``
    stack preserved on both history entries (the #812 fix keeps it)."""
    stack_impl = [
        {"workflow": "plan-orchestration", "step": "sub_impl"},
        {"workflow": "tmp-impl", "step": "step_1"},
        {"workflow": "dev-review-loop", "step": "implement"},
    ]
    stack_review = [
        {"workflow": "plan-orchestration", "step": "sub_impl"},
        {"workflow": "tmp-impl", "step": "step_1"},
        {"workflow": "dev-review-loop", "step": "review"},
    ]
    return {
        "version": 15,
        "workflow": "dev-review-loop",
        "step": "review",
        "agent": "review",
        "phase": "review",
        "history": [
            {"step": "implement", "agent": "dev", "summary": "", "position": stack_impl,
             "turn_outcome": {"status": "completed", "appreciation": "approved"}},
            {"step": "review", "agent": "review", "summary": "", "position": stack_review,
             "turn_outcome": {"status": "completed", "appreciation": "approved"}},
        ],
        "parent_workflow": "plan-orchestration",
        "parent_workflow_step": "sub_impl",
        "scaffold_branch": SCAFFOLD_BRANCH,
        "turn_outcome": {"status": "completed", "appreciation": "approved"},
        "resolution": {
            "selected_workflow": "dev-review-loop",
            "selected_step": "review",
        },
        "position": stack_review,
    }


class TestBranchMountUnwindByDeclaredRef:
    """The exit path must locate an intermediate branch mount by the
    *declared* mount reference, not its declared ``name`` (issue #812)."""

    def test_review_approved_unwinds_to_step2_via_declared_mount_ref(
        self, strict_branch_only_workflows_dir: Path, prompt_base: str,
    ) -> None:
        state = _step1_review_approved_full_stack_state()
        result = resolve_config(
            _trigger_body(state), prompt_base,
            str(strict_branch_only_workflows_dir), is_bot=True,
        )
        assert result["WORKFLOW_NAME"] == "dev-review-loop"
        assert result["WORKFLOW_STEP"] == "implement"
        assert "tmp-impl/step_2" in result.get("WORKFLOW_POSITION_PATH", "")
        # Negative: the wrong hop to the outer clean step must not happen.
        assert result.get("WORKFLOW_STEP") != "sub_clean"
        assert "plan-cleanup" not in result.get("STEP_SKILLS", "")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
