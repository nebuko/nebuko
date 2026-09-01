"""End-to-end integration tests for the 3-tier plan-orchestration flow (issue #470).

Exercises the FULL trigger -> routing -> result pipeline (``resolve_config``
facade + ``_build_result``) against a repo-root fixture mirroring production:
the real ``workflows/`` directory plus a scaffold-shaped
``tmp_impl/workflow.yaml`` whose steps are pure ``dev-review-loop`` refs.

Topology under test (3 tiers deep):

    plan-orchestration.scaffold
      -> tmp_impl/workflow.yaml (path-mounted, no agents)
        -> dev-review-loop (implement <-> review, terminal on approved)

Parent-field contract asserted throughout (verified against the resolver;
see issue #470 analysis):

* ``PARENT_WORKFLOW``/``PARENT_WORKFLOW_STEP`` are emitted ONLY when they
  change (entry/exit sub-workflow markers). On ordinary linear rounds the
  resolver emits ``''`` as a sentinel meaning "unchanged"; the GHA side
  (``build_output``) converts ``''`` -> ``None`` and ``build_state`` then
  inherits the values from the previous state. Bubble-out reads the
  persisted state's parent fields directly, never the emitted env var -- so
  the empty emission is by design, not a defect.
* ``PARENT_CHAIN`` (JSON, newest-first) IS emitted every round; it is the
  authoritative ancestor list for multi-tier nesting and is popped on each
  bubble-out tier.

Scenarios (issue #470 acceptance criteria):
  1. Full happy path: scaffold entry -> per-step dev-review loops ->
     multi-tier bubble-out -> sub_clean -> final_review -> terminal.
  2. Rejection loop confined to the sub-workflow scope.
  3. Resume after interruption mid-sub-workflow (state deserialization).
  4. Malformed ``tmp_impl/workflow.yaml`` -> graceful halt, no crash.
  5. Missing ``tmp_impl/`` -> exact scaffold-hint error message.

No mocks: real workflow files, real resolver/routing code, ``tmp_path`` only.
"""

import shutil
from pathlib import Path

import pytest
import yaml

import sys

sys.path.insert(0, str(Path(__file__).parent))

PROMPT_BASE = ".agents/prompts"
TMP_IMPL_REF = "tmp_impl/workflow.yaml"
# Newest-first ancestor chain while inside the per-step dev-review-loop tiers.
CHAIN_2 = [TMP_IMPL_REF, "plan-orchestration"]
CHAIN_2_JSON = '["tmp_impl/workflow.yaml","plan-orchestration"]'


def _dispatch(status: str, **kw):
    from workflow_state.sub_domains.dispatch.dispatch import Dispatch
    return Dispatch(status=status, **kw)


def _state_block(state) -> str:
    """Serialize a WorkflowState as the collapsed JSON block agents post."""
    from workflow_state.workflow_state import STATE_MARKER_START, STATE_MARKER_END
    return (
        f"<!-- bot-agent-dev -->\nbody\n\n```json\n"
        f"{STATE_MARKER_START}\n{state.model_dump_json(exclude_defaults=True)}\n"
        f"{STATE_MARKER_END}\n```\n"
    )


def _run(trigger_body: str, workflows_dir: Path) -> dict:
    """Run the full trigger -> config -> result pipeline for one trigger."""
    from resolve_config.resolve_config import resolve_config
    return resolve_config(
        trigger_body, PROMPT_BASE, str(workflows_dir), is_bot=True
    )


def _posted_state(
    *,
    workflow: str,
    step: str,
    agent: str,
    parent_workflow: str | None = None,
    parent_workflow_step: str | None = None,
    parent_chain: list[str] | None = None,
    dispatch=None,
    version: int = 1,
):
    """Build the explicit state a previous round would have posted.

    E2E tests drive each round with an explicit snapshot of the state the
    previous round's ``build_output`` step persisted (no implicit threading),
    so every assertion maps 1:1 to what the pipeline sees on that trigger.
    """
    from workflow_state import WorkflowState

    state = WorkflowState(
        version=version,
        workflow=workflow,
        step=step,
        agent=agent,
        parent_workflow=parent_workflow,
        parent_workflow_step=parent_workflow_step,
        parent_chain=parent_chain,
    )
    if dispatch is not None:
        state.dispatch = dispatch
    return state


def _in_review(parent_step: str = "step_1", **dispatch_kw):
    """Mid-loop snapshot: dev-review-loop.review inside a tmp_impl step."""
    return _posted_state(
        workflow="dev-review-loop",
        step="review",
        agent="review",
        parent_workflow=TMP_IMPL_REF,
        parent_workflow_step=parent_step,
        parent_chain=list(CHAIN_2),
        dispatch=_dispatch("advance", **dispatch_kw),
    )


def _in_impl(parent_step: str = "step_1"):
    """Mid-cycle snapshot: dev-review-loop.implement inside a tmp_impl step."""
    return _posted_state(
        workflow="dev-review-loop",
        step="implement",
        agent="dev",
        parent_workflow=TMP_IMPL_REF,
        parent_workflow_step=parent_step,
        parent_chain=list(CHAIN_2),
        dispatch=_dispatch("advance"),
    )


@pytest.fixture
def plan_repo(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Repo-root fixture: real workflows/ copied in + scaffold tmp_impl/.

    Mirrors the production layout: ``workflows/`` holds the static workflow
    definitions, ``tmp_impl/`` is scaffold-generated at the repo root and is
    referenced by path-relative refs (``tmp_impl/workflow.yaml``).
    """
    cur = Path(__file__).resolve().parent
    root_src = None
    for parent in [cur, *cur.parents]:
        if (parent / "workflows").is_dir() and (parent / ".agents").is_dir():
            root_src = parent
            break
    assert root_src is not None, "could not locate repo root above the test file"

    root = tmp_path / "root"
    wf_dst = root / "workflows"
    shutil.copytree(root_src / "workflows", wf_dst)

    tmp_impl = root / "tmp_impl"
    tmp_impl.mkdir()
    (tmp_impl / "workflow.yaml").write_text(yaml.dump({
        "name": "tmp-impl",
        "description": "Scaffold-generated per-step plan workflow",
        "trigger": {"agents": ["dev"]},
        "steps": [
            {
                "name": "step_1",
                "workflow": "dev-review-loop",
                "skill": ["tmp_impl/skills/step_1"],
                "next": "step_2",
            },
            {
                "name": "step_2",
                "workflow": "dev-review-loop",
                "skill": ["tmp_impl/skills/step_2"],
                "next": None,
            },
        ],
    }))
    (tmp_impl / "skills").mkdir()
    (tmp_impl / "skills" / "step_1.md").write_text("# Step 1\n")
    (tmp_impl / "skills" / "step_2.md").write_text("# Step 2\n")

    # Path-relative refs (tmp_impl/...) resolve against the cwd (repo root).
    monkeypatch.chdir(root)
    return root, wf_dst


class TestPlanOrchestrationHappyPath:
    """Scenario 1: full 3-tier lifecycle through the trigger pipeline.

    Each round posts an explicit state snapshot (what ``build_output`` would
    persist) and asserts the emitted routing contract, including the
    empty-``PARENT_WORKFLOW`` sentinel on linear rounds.
    """

    def test_full_lifecycle_scaffold_to_terminal(self, plan_repo):
        root, wf_dst = plan_repo

        # Round 1: dev finishes scaffold -> entry into tmp_impl sub-workflow.
        s = _posted_state(
            workflow="plan-orchestration", step="scaffold", agent="dev",
            dispatch=_dispatch("advance"),
        )
        r1 = _run(_state_block(s), wf_dst)
        assert r1["SKIP_AGENT"] == "false"
        assert r1["TASK"] == "dev"
        assert r1["WORKFLOW_NAME"] == "dev-review-loop"
        assert r1["WORKFLOW_STEP"] == "implement"
        # Entry marker: the effective parent is the reloadable path-mounted
        # ref (Bug B/D), with the resume point at the child's entry step;
        # the grandparent is carried in the chain for the multi-tier bubble.
        assert r1["PARENT_WORKFLOW"] == TMP_IMPL_REF
        assert r1["PARENT_WORKFLOW_STEP"] == "step_1"
        assert r1["PARENT_CHAIN"] == CHAIN_2_JSON

        # Round 2: implement done -> review (linear, inside dev-review-loop).
        # Linear rounds emit the empty sentinel: the GHA side inherits the
        # parent fields from the posted state, which must stay intact.
        r2 = _run(_state_block(_in_impl(parent_step="step_1")), wf_dst)
        assert r2["SKIP_AGENT"] == "false"
        assert (r2["TASK"], r2["WORKFLOW_STEP"]) == ("review", "review")
        assert r2["WORKFLOW_NAME"] == "dev-review-loop"
        assert r2["PARENT_WORKFLOW"] == ""
        assert r2["PARENT_WORKFLOW_STEP"] == ""
        assert r2["PARENT_CHAIN"] == CHAIN_2_JSON

        # Round 3: review rejects -> re-implement (loop confined to scope).
        r3 = _run(_state_block(_in_review(branch="rejected")), wf_dst)
        assert r3["SKIP_AGENT"] == "false"
        assert (r3["TASK"], r3["WORKFLOW_STEP"]) == ("dev", "implement")
        assert r3["WORKFLOW_NAME"] == "dev-review-loop"
        # No parent refresh on a rejection: nothing leaves the inner tier.
        assert r3["PARENT_WORKFLOW"] == ""
        assert r3["PARENT_CHAIN"] == CHAIN_2_JSON

        # Round 4: re-implement done -> review again (same confinement).
        r4 = _run(_state_block(_in_impl(parent_step="step_1")), wf_dst)
        assert (r4["TASK"], r4["WORKFLOW_STEP"]) == ("review", "review")
        assert r4["PARENT_CHAIN"] == CHAIN_2_JSON

        # Round 5: review approves step_1 -> chain into step_2 (same tier).
        # step_2 is another dev-review-loop ref of tmp_impl: re-entry
        # refreshes the immediate parent's resume point to step_2.
        r5 = _run(_state_block(_in_review(branch="approved")), wf_dst)
        assert r5["SKIP_AGENT"] == "false"
        assert (r5["TASK"], r5["WORKFLOW_STEP"]) == ("dev", "implement")
        assert r5["WORKFLOW_NAME"] == "dev-review-loop"
        assert r5["PARENT_WORKFLOW"] == TMP_IMPL_REF
        assert r5["PARENT_WORKFLOW_STEP"] == "step_2"
        assert r5["PARENT_CHAIN"] == CHAIN_2_JSON

        # Round 6: step_2 implement done -> review.
        r6 = _run(_state_block(_in_impl(parent_step="step_2")), wf_dst)
        assert (r6["TASK"], r6["WORKFLOW_STEP"]) == ("review", "review")
        assert r6["PARENT_CHAIN"] == CHAIN_2_JSON

        # Round 7: step_2 review approved (terminal) -> BUBBLE TWO TIERS
        # past the intermediate tmp_impl tier to plan-orchestration.sub_clean.
        r7 = _run(_state_block(_in_review(parent_step="step_2", branch="approved")), wf_dst)
        assert r7["SKIP_AGENT"] == "false"
        assert (r7["TASK"], r7["WORKFLOW_STEP"]) == ("dev", "sub_clean")
        assert r7["WORKFLOW_NAME"] == "plan-orchestration"
        # Multi-tier bubble-out fully clears the ancestor chain (Bug C).
        assert r7["PARENT_CHAIN"] == "[]"

        # Round 8: sub_clean done -> final_review (no sub-workflow left).
        s = _posted_state(
            workflow="plan-orchestration", step="sub_clean", agent="dev",
            parent_chain=[], dispatch=_dispatch("advance"),
        )
        r8 = _run(_state_block(s), wf_dst)
        assert r8["SKIP_AGENT"] == "false"
        assert (r8["TASK"], r8["WORKFLOW_STEP"]) == ("review", "final_review")
        assert r8["WORKFLOW_NAME"] == "plan-orchestration"
        assert r8["PARENT_CHAIN"] == "[]"

        # Round 9: final_review approved -> overall terminal (graceful halt).
        s = _posted_state(
            workflow="plan-orchestration", step="final_review", agent="review",
            parent_chain=[], dispatch=_dispatch("advance", branch="approved"),
        )
        r9 = _run(_state_block(s), wf_dst)
        assert r9["SKIP_AGENT"] == "true"


class TestChainBoundaries:
    """The parent chain is non-empty only while inside the sub-workflow
    tiers and is cleared exactly at the multi-tier bubble-out (last round)."""

    def test_history_and_chain_boundaries(self, plan_repo):
        root, wf_dst = plan_repo

        snapshots = [
            _posted_state(
                workflow="plan-orchestration", step="scaffold", agent="dev",
                dispatch=_dispatch("advance"),
            ),
            _in_impl(parent_step="step_1"),                  # -> review
            _in_review(branch="approved"),             # -> step_2 implement
            _in_impl(parent_step="step_2"),                  # -> review
            _in_review(parent_step="step_2", branch="approved"),  # -> bubble
        ]

        rounds = [_run(_state_block(snap), wf_dst) for snap in snapshots]
        assert all(r["SKIP_AGENT"] == "false" for r in rounds)

        # Chain present inside sub-workflow tiers, cleared after bubble-out.
        for r in rounds[:-1]:
            assert r["PARENT_CHAIN"] == CHAIN_2_JSON
        assert rounds[-1]["PARENT_CHAIN"] == "[]"
        assert rounds[-1]["WORKFLOW_STEP"] == "sub_clean"


class TestRejectionLoopScope:
    """Scenario 2: a review rejection loops within dev-review-loop and never
    leaks out to the intermediate or top-tier workflows."""

    def test_rejection_stays_in_sub_scope(self, plan_repo):
        root, wf_dst = plan_repo

        result = _run(_state_block(_in_review(branch="rejected")), wf_dst)
        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == "dev-review-loop"
        assert result["WORKFLOW_STEP"] == "implement"
        # No parent refresh and the full ancestor chain survives the loop:
        # the rejection is scope-local to the innermost tier.
        assert result["PARENT_WORKFLOW"] == ""
        assert result["PARENT_WORKFLOW_STEP"] == ""
        assert result["PARENT_CHAIN"] == CHAIN_2_JSON


class TestResumeAfterInterruption:
    """Scenario 3: deserialize a state saved mid-sub-workflow
    (step_2.review inside the full 3-tier chain) and resume correctly."""

    def test_resume_at_step2_review_bubbles_out(self, plan_repo):
        root, wf_dst = plan_repo
        from workflow_state.features.get_last_state.get_last_state import (
            extract_state_from_body,
        )

        # A posted state block: saved mid-step_2 review after interruption.
        # The chain MUST carry both ancestors -- the inner tier needs the
        # intermediate path-mounted ref to reload the tmp_impl tier before
        # bubbling back to plan-orchestration.
        posted = _in_review(parent_step="step_2", branch="approved")
        posted.version = 3
        body = _state_block(posted)
        restored = extract_state_from_body(body)
        assert restored is not None
        assert restored.step == "review"
        assert restored.parent_chain == CHAIN_2

        # Next trigger: the restored state already carries dispatch.advance.
        result = _run(_state_block(restored), wf_dst)
        # Resumes at the step_2 terminal -> bubbles two tiers to sub_clean.
        assert result["SKIP_AGENT"] == "false"
        assert (result["TASK"], result["WORKFLOW_STEP"]) == ("dev", "sub_clean")
        assert result["WORKFLOW_NAME"] == "plan-orchestration"
        assert result["PARENT_CHAIN"] == "[]"

    def test_resume_without_dispatch_falls_back_to_state_step(self, plan_repo):
        root, wf_dst = plan_repo

        # State saved mid-implement with NO dispatch (crash between posting
        # the state and the dispatch) -> engine resumes from the state step.
        s = _posted_state(
            workflow="dev-review-loop", step="implement", agent="dev",
            parent_workflow=TMP_IMPL_REF, parent_workflow_step="step_1",
            parent_chain=list(CHAIN_2),
            version=2,
        )
        result = _run(_state_block(s), wf_dst)
        assert result["SKIP_AGENT"] == "false"
        assert (result["TASK"], result["WORKFLOW_STEP"]) == ("review", "review")
        # Linear-round sentinel: parent inherited from the posted state.
        assert result["PARENT_WORKFLOW"] == ""
        assert result["PARENT_CHAIN"] == CHAIN_2_JSON

    def test_resume_with_truncated_chain_halts_gracefully(self, plan_repo):
        root, wf_dst = plan_repo

        # A state whose chain lost its intermediate tier (e.g. persisted by
        # pre-#470 code) cannot re-load the tmp_impl tier on the way out:
        # the pipeline must halt with a clear reason, never crash.
        s = _in_review(parent_step="step_2", branch="approved")
        s.parent_chain = ["plan-orchestration"]
        s.dispatch = _dispatch("advance", branch="approved")
        result = _run(_state_block(s), wf_dst)
        assert result["SKIP_AGENT"] == "true"
        assert result["REASON"]


class TestMalformedPlanWorkflow:
    """Scenario 4: malformed tmp_impl/workflow.yaml -> clear parse error,
    GHA-run-friendly halt semantics (no silent crash)."""

    def test_malformed_yaml_halts_pipeline_cleanly(self, plan_repo):
        root, wf_dst = plan_repo

        (root / "tmp_impl" / "workflow.yaml").write_text(
            "name: bad\n  steps: [ {unclosed\n"
        )
        s = _posted_state(
            workflow="plan-orchestration", step="scaffold", agent="dev",
            dispatch=_dispatch("advance"),
        )
        result = _run(_state_block(s), wf_dst)

        # Halts cleanly with a diagnostic, never crashes or runs an agent.
        assert result["SKIP_AGENT"] == "true"
        assert "No valid task" in result["REASON"]

    def test_load_workflow_reports_file_and_line(self, plan_repo):
        """load_workflow (the GHA pre-check's validation entry point) reports
        the offending file and line for malformed YAML."""
        root, wf_dst = plan_repo
        from config import load_workflow

        (root / "tmp_impl" / "workflow.yaml").write_text(
            "name: bad\n  steps: [ {unclosed\n"
        )
        with pytest.raises(ValueError) as exc_info:
            load_workflow("tmp_impl/workflow.yaml")
        msg = str(exc_info.value)
        assert "tmp_impl/workflow.yaml" in msg
        assert "line" in msg.lower()


class TestMissingPlanWorkflow:
    """Scenario 5: missing tmp_impl/ -> exact scaffold-hint error message."""

    EXACT_MSG = (
        "Plan workflow file not found at tmp_impl/workflow.yaml. "
        "Was the scaffold step executed successfully?"
    )

    def test_missing_tmp_impl_loads_exact_message(self, plan_repo):
        root, wf_dst = plan_repo
        from config import load_workflow

        shutil.rmtree(root / "tmp_impl")
        with pytest.raises(FileNotFoundError) as exc_info:
            load_workflow("tmp_impl/workflow.yaml")
        assert str(exc_info.value) == self.EXACT_MSG

    def test_missing_tmp_impl_halts_pipeline_cleanly(self, plan_repo):
        root, wf_dst = plan_repo

        shutil.rmtree(root / "tmp_impl")
        s = _posted_state(
            workflow="plan-orchestration", step="scaffold", agent="dev",
            dispatch=_dispatch("advance"),
        )
        result = _run(_state_block(s), wf_dst)
        # Graceful halt (no crash); the actionable diagnosis is produced by
        # the GHA pre-check via load_workflow (covered above).
        assert result["SKIP_AGENT"] == "true"
        assert "No valid task" in result["REASON"]