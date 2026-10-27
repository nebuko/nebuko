"""Gate-decision routing tests (issue #648).

Covers the engine-owned gate release: ``/nebuko approve gate`` /
``/nebuko reject gate`` are validated against an identity-verified human
author, applied single-shot per hold instance, and routed through the gated
step's ``on_result`` conditions — never through an agent re-invocation.
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
sys.path.insert(
    0,
    str(
        Path(__file__).parents[4]
        / "lib" / "agent_script" / "features" / "build_output"
    ),
)

from config import StepRef  # noqa: E402

from resolve_config import resolve_config  # noqa: E402
from resolve_config.features.resolve_route.resolve_route import (  # noqa: E402
    _resolve_gate_decision_path,
    _resolve_resume_path,
)
from resolve_config.sub_domains.author_identity.author_identity import (  # noqa: E402
    AuthorIdentity,
)
from resolve_config.sub_domains.workflow_command.workflow_command import (  # noqa: E402
    parse_workflow_command,
)
from workflow_state import (  # noqa: E402
    Appreciation,
    GateDecision,
    GateLock,
    GateStatus,
    IncompleteReason,
    TurnOutcome,
    TurnStatus,
    WorkflowState,
)


GATED_WORKFLOW_YAML = (
    "name: default\n"
    "description: Standard workflow with a gated approval step\n"
    "steps:\n"
    "  - name: plan\n"
    "    agent: architect\n"
    "    next: stub_approval\n"
    "  - name: stub_approval\n"
    "    agent: architect\n"
    "    human_gate: true\n"
    "    next: implement\n"
    "    conditions:\n"
    "      - on_result: approved\n"
    "        next: implement\n"
    "      - on_result: rejected\n"
    "        next: plan\n"
    "  - name: implement\n"
    "    agent: dev\n"
    "    next: review\n"
    "  - name: review\n"
    "    agent: review\n"
    "    next: null\n"
)

HUMAN = AuthorIdentity(
    login="olivier", is_bot=False, association="MEMBER", is_machine_comment=False
)
UNAUTHORIZED = AuthorIdentity(
    login="random", is_bot=False, association="NONE", is_machine_comment=False
)
BOT = AuthorIdentity(
    login="github-actions[bot]", is_bot=True, association="NONE", is_machine_comment=False
)


def _held_state(
    *,
    step: str = "stub_approval",
    gate: GateLock | None = None,
    gate_history=None,
) -> WorkflowState:
    """A state parked on its ``human_gate`` (issues #964, #1033).

    A gate hold is engine-owned run state: the step declares
    ``human_gate: true`` and the root :class:`GateLock` is armed. The agent's
    own verdict is ``approved`` — the lock, not the agent, blocks the advance.
    """
    return WorkflowState(
        workflow="default",
        step=step,
        agent="architect",
        turn_outcome=TurnOutcome(
            status=TurnStatus.COMPLETED,
            appreciation=Appreciation.APPROVED,
        ),
        position=[StepRef(workflow="default", step=step)],
        gate_history=list(gate_history or []),
        gate=gate if gate is not None else GateLock(step=step),
    )


def _write_workflow(tmpdir: str) -> str:
    path = os.path.join(tmpdir, "default.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(GATED_WORKFLOW_YAML)
    return tmpdir


def _cmd(text: str):
    return parse_workflow_command(text)


class TestResolveGateDecisionPath(unittest.TestCase):
    """Unit tests for ``_resolve_gate_decision_path``."""

    def test_missing_state_is_inert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                None, _cmd("/nebuko approve gate"), HUMAN, tmpdir
            )
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        # The benign kind (issue #1012) keeps the guards phase at exit 0.
        self.assertEqual(cfg.get("SKIP_KIND"), "gate_noop")

    def test_unauthorized_author_is_inert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                _held_state(), _cmd("/nebuko approve gate"), UNAUTHORIZED, tmpdir
            )
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg.get("SKIP_KIND"), "gate_noop")
        self.assertIn("not authorized", cfg.get("REASON", ""))

    def test_bot_author_is_inert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                _held_state(), _cmd("/nebuko approve gate"), BOT, tmpdir
            )
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg.get("SKIP_KIND"), "gate_noop")

    def test_not_held_is_inert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            state = _held_state(gate=GateLock(step="stub_approval", status=GateStatus.RELEASED))
            cfg = _resolve_gate_decision_path(
                state, _cmd("/nebuko approve gate"), HUMAN, tmpdir
            )
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg.get("SKIP_KIND"), "gate_noop")
        self.assertEqual(
            cfg.get("REASON"), "gate: workflow is not held at a human_gate step"
        )

    def test_approve_routes_to_on_result_approved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                _held_state(), _cmd("/nebuko approve gate"), HUMAN, tmpdir
            )
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg["task"], "dev")
        self.assertEqual(cfg["config_step"], "implement")
        decision = json.loads(cfg["config_gate_decision"])
        self.assertEqual(decision["status"], "approved")
        self.assertEqual(decision["decided_by"], "olivier")

    def test_reject_with_feedback_reruns_gated_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                _held_state(),
                _cmd("/nebuko reject gate --feedback fix the X section"),
                HUMAN,
                tmpdir,
            )
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        # Re-runs the gated step's own agent, not the on_result successor.
        self.assertEqual(cfg["task"], "architect")
        self.assertEqual(cfg["config_step"], "stub_approval")
        decision = json.loads(cfg["config_gate_decision"])
        self.assertEqual(decision["status"], "rejected")
        self.assertEqual(decision["feedback"], "fix the X section")

    def test_reject_with_multiline_feedback_keeps_newlines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                _held_state(),
                _cmd(
                    "/nebuko reject gate --feedback\n"
                    "1. Flat\n"
                    "2. Remove this section"
                ),
                HUMAN,
                tmpdir,
            )
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg["task"], "architect")
        self.assertEqual(cfg["config_step"], "stub_approval")
        decision = json.loads(cfg["config_gate_decision"])
        self.assertEqual(decision["status"], "rejected")
        self.assertEqual(
            decision["feedback"], "1. Flat\n2. Remove this section"
        )

    def test_approve_with_multiline_feedback_stores_verbatim(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                _held_state(),
                _cmd("/nebuko approve gate --feedback\n1. Flat\n2. Keep as is"),
                HUMAN,
                tmpdir,
            )
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        decision = json.loads(cfg["config_gate_decision"])
        self.assertEqual(decision["status"], "approved")
        self.assertEqual(decision["feedback"], "1. Flat\n2. Keep as is")

    def test_reject_without_feedback_routes_on_result_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                _held_state(), _cmd("/nebuko reject gate"), HUMAN, tmpdir
            )
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg["task"], "architect")
        self.assertEqual(cfg["config_step"], "plan")

    def test_duplicate_approve_is_single_shot_noop(self):
        held_at = [StepRef(workflow="default", step="stub_approval")]
        prior = GateDecision(
            status="approved",
            workflow="default",
            target_step="stub_approval",
            decided_by="olivier",
            decided_at=datetime.now(timezone.utc),
            position=held_at,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                _held_state(gate_history=[prior]),
                _cmd("/nebuko approve gate"),
                HUMAN,
                tmpdir,
            )
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertIn("single-shot", cfg.get("REASON", ""))


class TestResumeGuardOnGateHold(unittest.TestCase):
    """``/nebuko resume`` must not release a ``human_gate:*`` hold."""

    def test_resume_on_gate_hold_points_at_gate_commands(self):
        cfg = _resolve_resume_path(
            _held_state(),
            "resume",
            "/nonexistent",
            human_input="approved",
        )
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        reason = cfg.get("REASON", "")
        self.assertIn("approve gate", reason)
        self.assertIn("reject gate", reason)

    def test_resume_on_an_interrupted_step_still_resumes(self):
        """An exhausted soft-kill halt is resumable by a human (issue #704)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            state = WorkflowState(
                workflow="default",
                step="implement",
                agent="dev",
                turn_outcome=TurnOutcome(
                    status=TurnStatus.INCOMPLETE,
                    incomplete_reason=IncompleteReason.SOFT_TIMEOUT,
                ),
                position=[StepRef(workflow="default", step="implement")],
            )
            cfg = _resolve_resume_path(
                state, "resume", tmpdir, human_input="retry"
            )
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg["task"], "dev")


class TestGateCommandIntegration(unittest.TestCase):
    """End-to-end ``resolve_config`` behaviour for the gate commands."""

    @staticmethod
    def _setup_prompt_base():
        prompt_base = tempfile.mkdtemp()
        os.makedirs(os.path.join(prompt_base, "dev"), exist_ok=True)
        os.makedirs(os.path.join(prompt_base, "architect"), exist_ok=True)
        os.makedirs(os.path.join(prompt_base, "review"), exist_ok=True)
        for role in ("dev", "architect", "review"):
            with open(os.path.join(prompt_base, role, "system.md"), "w") as f:
                f.write(f"# {role} prompt")
        return prompt_base

    @staticmethod
    def _body(command: str, state: WorkflowState) -> str:
        state_json = state.model_dump_json()
        return "\n".join(
            [
                command,
                "<details>",
                "<summary>Workflow State</summary>",
                "",
                "```json",
                "<!-- WORKFLOW_STATE_START -->",
                state_json,
                "<!-- WORKFLOW_STATE_END -->",
                "```",
                "",
                "</details>",
            ]
        )

    def _resolve(self, command: str, env: dict, state: WorkflowState | None = None):
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            body = self._body(command, state or _held_state())
            with unittest.mock.patch.dict(os.environ, env, clear=False):
                return resolve_config(body, prompt_base, tmpdir, is_bot=False)

    def test_bot_authored_approve_is_inert(self):
        result = self._resolve(
            "/nebuko approve gate",
            {
                "TRIGGER_AUTHOR_LOGIN": "github-actions[bot]",
                "TRIGGER_AUTHOR_TYPE": "Bot",
                "TRIGGER_AUTHOR_ASSOCIATION": "NONE",
            },
        )
        self.assertEqual(result["SKIP_AGENT"], "true")

    def test_human_body_echoing_machine_token_is_inert(self):
        """A privileged human echoing agent output cannot carry a decision."""
        result = self._resolve(
            "/nebuko approve gate\n<!-- bot-trigger: dev -->",
            {
                "TRIGGER_AUTHOR_LOGIN": "olivier",
                "TRIGGER_AUTHOR_TYPE": "User",
                "TRIGGER_AUTHOR_ASSOCIATION": "MEMBER",
            },
        )
        self.assertEqual(result["SKIP_AGENT"], "true")

    def test_human_approve_routes_to_successor(self):
        result = self._resolve(
            "/nebuko approve gate",
            {
                "TRIGGER_AUTHOR_LOGIN": "olivier",
                "TRIGGER_AUTHOR_TYPE": "User",
                "TRIGGER_AUTHOR_ASSOCIATION": "MEMBER",
            },
        )
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["WORKFLOW_STEP"], "implement")

    def test_resume_on_gate_hold_is_inert(self):
        result = self._resolve(
            "/nebuko resume --feedback approved",
            {
                "TRIGGER_AUTHOR_LOGIN": "olivier",
                "TRIGGER_AUTHOR_TYPE": "User",
                "TRIGGER_AUTHOR_ASSOCIATION": "MEMBER",
            },
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("approve gate", result.get("REASON", ""))

    def test_reject_with_feedback_rerun_then_completion_blocks_advance(self):
        """Replay the issue payload (nebuko/website#32).

        An armed gate on ``plan`` is rejected with feedback; the gated step
        re-runs and completes. Because the re-run's completion re-arms a
        fresh gate, the next routing must resolve to a gate hold — never to
        ``implement`` — until a new ``/nebuko approve gate``.
        """
        from resolve_config.features.resolve_route.resolve_route import _gate_pending

        # The persisted re-run completion state must carry an ARMED lock so the
        # routing holds (see TestPostDecisionGateReArm for the build_output
        # integration that guarantees this persistence).
        rerun_completion = _held_state(step="plan", gate=GateLock(step="plan"))
        with self.subTest("persisted state must keep the gate armed"):
            self.assertTrue(
                rerun_completion.gate is not None
                and rerun_completion.gate.status is GateStatus.ARMED,
            )
        with self.subTest("routing must hold on the armed gate, never advance"):
            self.assertTrue(_gate_pending(rerun_completion))


class TestPostDecisionGateReArm(unittest.TestCase):
    """The re-run's completing turn must re-arm a fresh gate (issue #1178).

    End-to-end through ``build_output``: the re-run of a rejected-with-feedback
    gated step completes with ``GATE_DECISION`` (rejected, with feedback) and
    ``GATE_RELEASED=true`` riding along (resolve_route sets
    ``config_gate_released`` on this path too). The persisted gate must stay
    ARMED — an unconditional release after the re-arm used to flip it straight
    back to RELEASED and let the workflow advance without a new approval.
    """

    @staticmethod
    def _msg_event(text):
        from openhands.sdk.event import MessageEvent
        from openhands.sdk.llm import Message

        return MessageEvent(
            llm_message=Message(role="assistant", content=text), source="agent"
        )

    @staticmethod
    def _completed():
        return TurnOutcome(
            status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED
        )

    def test_reject_with_feedback_rerun_then_completion_keeps_gate_armed(self):
        import build_output as bo
        from types import SimpleNamespace as NS
        from workflow_state import GateLock, RunPrefs, WorkflowState

        # The re-run of the gated step ``plan`` carries the previous state
        # whose lock was consumed by the decision (released by the decision
        # resolution). The re-run's completion must raise a fresh ARMED lock.
        prior = WorkflowState(
            workflow="wf",
            step="plan",
            agent="architect",
            gate=GateLock(step="plan", status=GateStatus.RELEASED),
        )
        decision = GateDecision(
            status="rejected",
            workflow="wf",
            target_step="plan",
            decided_by="olivier-norture",
            decided_at=datetime.now(timezone.utc),
            feedback="Use Posthog",
            position=[StepRef(workflow="wf", step="plan")],
        )
        captured = {}

        def _capture_build_state(prev_state, **kwargs):
            captured.update(kwargs)
            return NS(run_id=2)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "wf.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "name: wf\n"
                    'description: "test workflow"\n'
                    "steps:\n"
                    "  - name: plan\n"
                    "    agent: architect\n"
                    "    human_gate: true\n"
                    "    next: implement\n"
                )
            env = {
                "WORKFLOWS_DIR": tmpdir,
                "WORKFLOW_NAME": "wf",
                "WORKFLOW_STEP": "plan",
                "ROLE_NAME": "architect",
                "GATE_DECISION": decision.model_dump_json(),
                "GATE_RELEASED": "true",
            }
            conversation = NS(
                state=NS(events=[self._msg_event("plan answer")]),
                id="conv-1178-rearm",
                run_outcome=None,
            )
            metrics = NS(review_fix_requests=0, to_dict=lambda: {})
            with unittest.mock.patch.object(bo, "get_agent_final_response", return_value="plan answer"), \
                 unittest.mock.patch.object(bo, "_load_prev_state", return_value=prior), \
                 unittest.mock.patch.object(bo, "_build_issue_info", return_value=None), \
                 unittest.mock.patch.object(bo, "_build_pr_info", return_value=None), \
                 unittest.mock.patch.object(
                     bo, "_resolve_outcome",
                     return_value=(self._completed(), RunPrefs(), None),
                 ), \
                 unittest.mock.patch.object(bo, "clear_run"), \
                 unittest.mock.patch.object(bo, "clear_call_agent_run"), \
                 unittest.mock.patch.object(bo, "clear_scaffold_branch_run"), \
                 unittest.mock.patch.object(bo, "clear_complexity_review_run"), \
                 unittest.mock.patch.object(bo, "get_recorded_review_request", return_value=None), \
                 unittest.mock.patch.object(bo, "build_state", side_effect=_capture_build_state), \
                 unittest.mock.patch.object(bo, "split_message", return_value=[]), \
                 unittest.mock.patch.object(bo, "_write_output_files", return_value=None), \
                 unittest.mock.patch.dict(os.environ, env, clear=False):
                cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    bo.build_output(conversation, metrics)
                finally:
                    os.chdir(cwd)

        gate = captured["gate"]
        self.assertIsNotNone(gate)
        self.assertEqual(gate.step, "plan")
        self.assertIs(gate.status, GateStatus.ARMED)
        self.assertTrue(gate.armed())


if __name__ == "__main__":
    unittest.main()
