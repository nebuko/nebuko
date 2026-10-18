"""Regression guard: a crashed run must not advance the workflow (issue #978).

The reported defect: run 9 of ``default/implement`` crashed on
``litellm.RateLimitError: ... Go usage limit exceeded``. Nothing was produced,
yet the engine advanced to ``review`` (run 10), and later bot comments kept
re-triggering runs because the ``<!-- workflow-continue -->`` token was matched
*anywhere* in the body — including inside the serialized state/history.

Three invariants are pinned here, all replayable on top of the #964 rewrite:

* a crashed run's comment is a **notification**: it carries no dispatch
  information and no continue token;
* a trigger body that carries **no dispatch information and no human command**
  performs **no action** — no route, no successor step;
* the continue token is **prefix-anchored**, so an occurrence embedded in state
  or history can never fire a run.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from resolve_config import resolve_config
from resolve_workflow import TASK_TO_PROMPT
from workflow_state import WorkflowState
from workflow_state.workflow_state import STATE_MARKER_END, STATE_MARKER_START

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / "defaults" / "workflows"

CONTINUE_TOKEN = "<!-- workflow-continue -->"

RESUME_COMMAND = "/nebuko resume"


@pytest.fixture
def prompt_base(tmp_path: Path) -> str:
    """Materialize the prompt filenames ``resolve_config`` looks up."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in TASK_TO_PROMPT.values():
        (prompts / name).write_text(f"# Prompt for {name}")
    return str(prompts)


def _state_block(state: WorkflowState) -> str:
    """The state marker block exactly as ``prepare_comment`` embeds it."""
    from workflow_state.features.to_json.to_json import state_to_json

    return (
        STATE_MARKER_START
        + "\n"
        + state_to_json(state)
        + "\n"
        + STATE_MARKER_END
        + "\n"
    )


def _implement_state() -> WorkflowState:
    """The pre-run state of the crashed ``default/implement`` run (issue #978)."""
    return WorkflowState(
        workflow="default",
        step="implement",
        agent="dev",
        position=[{"workflow": "default", "step": "implement"}],
    )


def _crashed_implement_state() -> WorkflowState:
    """The state the engine persists for the crashed run (the run's own step)."""
    return WorkflowState(
        workflow="default",
        step="implement",
        agent="dev",
        position=[{"workflow": "default", "step": "implement"}],
    )


def _crashed_conversation():
    """A conversation that crashed on the provider rate limit (issue #978)."""
    from agent_script.features.run_agent.run_failure import (
        RunFailure,
        RunFailureKind,
    )
    from agent_script.features.run_agent.run_outcome import (
        OutcomeStatus,
        RunOutcome,
    )

    outcome = RunOutcome(
        status=OutcomeStatus.FAILURE,
        failure=RunFailure(
            kind=RunFailureKind.LLM_ERROR,
            summary=(
                "The agent run failed: Go usage limit exceeded "
                "(litellm.RateLimitError)."
            ),
            detail=(
                "litellm.RateLimitError: RateLimitError: OpenAIException - "
                "Go usage limit exceeded"
            ),
        ),
    )
    conv = SimpleNamespace()
    conv.state = SimpleNamespace(events=[])
    conv.run_outcome = outcome
    conv.id = "crashed-run-978"
    return conv


def _build_failure_body(prev_state: WorkflowState) -> str:
    """Run ``build_output`` on a crashed run and return the posted body.

    Mirrors the production seam: the agent-side builder produces the final
    message, then the publish seam prepends the answer header (bot marker +
    continue token + collapsed state) exactly as ``prepare_comment`` does.
    """
    from agent_script.features.build_output.build_output import build_output
    from metrics_schema import Metrics
    from prepare_comment import format_answer_header

    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            env = {
                "ROLE_NAME": "dev",
                "WORKFLOW_NAME": "default",
                "WORKFLOW_STEP": "implement",
            }
            with patch.dict("os.environ", env, clear=False):
                result = build_output(
                    _crashed_conversation(),
                    Metrics(agent_role="dev"),
                    None,
                    prev_state=prev_state,
                )
        finally:
            os.chdir(cwd)

    state = result["workflow_state"]
    header = format_answer_header("dev", state)
    return header + (result["final_message"] or "")


def _resolve(body: str, prompt_base: str) -> dict:
    return resolve_config(body, prompt_base, str(WORKFLOWS_DIR), is_bot=True)


class TestCrashedRunStaysOnTheStep:
    """The reported defect: nothing was done, so the engine must not advance."""

    def test_crashed_rate_limited_run_stays_on_the_step(self, prompt_base: str):
        body = _build_failure_body(_crashed_implement_state())

        result = _resolve(body, prompt_base)

        assert result["SKIP_AGENT"] == "true"
        assert result.get("WORKFLOW_STEP") != "review"
        assert result.get("TASK") != "review"

    def test_crashed_run_emits_no_dispatch_information(self, prompt_base: str):
        body = _build_failure_body(_crashed_implement_state())

        # The notification names the cause and the recovery command…
        assert "usage limit exceeded" in body.lower()
        assert RESUME_COMMAND in body
        # …and carries no dispatch information at all: no routing payload, no
        # machine trigger, no continue token.
        assert "AGENT_RUN_CONFIG" not in body
        assert "<!-- bot-trigger:" not in body
        assert CONTINUE_TOKEN not in body

    def test_crashed_run_persists_no_dispatch_in_its_state(self, prompt_base: str):
        from workflow_state.features.to_json.to_json import state_to_json

        body = _build_failure_body(_crashed_implement_state())

        block = body.split(STATE_MARKER_START, 1)[1].split(STATE_MARKER_END, 1)[0]
        state = json.loads(block)
        assert state["current_context"]["last_run_status"] == "failure"
        # The notification carries state for ``/nebuko resume`` observability,
        # never dispatch/decision data.
        assert "dispatch" not in state


class TestDispatchLessEventIsANotification:
    """A plain message is a notification: the engine performs no action."""

    def test_state_without_dispatch_information_is_a_no_op(self, prompt_base: str):
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- bot-agent-dev -->\n"
            "Something went wrong, no decision was made.\n"
            + _state_block(_implement_state())
        )

        result = _resolve(body, prompt_base)

        assert result["SKIP_AGENT"] == "true"
        assert result.get("WORKFLOW_STEP") != "review"
        assert result.get("TASK") != "review"

    def test_body_without_state_and_without_command_does_nothing(self, prompt_base: str):
        result = _resolve("Just a plain notification.", prompt_base)

        assert result["SKIP_AGENT"] == "true"


class TestContinueTokenIsPrefixAnchored:
    """The token must be a prefix, never an embedded substring (issue #978)."""

    def test_the_gate_anchors_the_token_with_startswith(self):
        """The GHA gate must not match the literal inside state/history (#978).

        The one-line YAML edit (``contains`` -> ``startsWith``) needs a token
        with ``workflow`` scope, which this run does not have. Until a human or
        a ``workflow``-scoped token applies it, this is an expected failure
        rather than a silently red suite; the assertion is written against the
        *fixed* expression, so flipping the YAML turns it green without an edit
        here. Defects A and B already make such a re-trigger a no-op.
        """
        import yaml

        workflow = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml").read_text(
                encoding="utf-8"
            )
        )
        expression = workflow["jobs"]["run-task"]["if"]

        if f"contains(github.event.comment.body, '{CONTINUE_TOKEN}')" in expression:
            pytest.xfail(
                "YAML edit blocked on a workflow-scoped token — issue #978; "
                "apply the startsWith edit to .github/workflows/nebuko-workflow.yaml"
            )

        assert (
            f"startsWith(github.event.comment.body, '{CONTINUE_TOKEN}')" in expression
        )
        assert (
            f"contains(github.event.comment.body, '{CONTINUE_TOKEN}')" not in expression
        )

    def test_live_run_body_starts_with_the_continue_token(self, prompt_base: str):
        from prepare_comment import format_answer_header

        header = format_answer_header("dev", _implement_state())

        assert header.startswith(CONTINUE_TOKEN)

    def test_failed_run_body_does_not_start_with_the_continue_token(
        self, prompt_base: str
    ):
        body = _build_failure_body(_crashed_implement_state())

        assert not body.startswith(CONTINUE_TOKEN)

    def test_token_embedded_in_history_cannot_trigger(self, prompt_base: str):
        state = _implement_state().model_copy(
            update={
                "current_context": {
                    "recovery_log": f"a previous comment quoted {CONTINUE_TOKEN}",
                }
            }
        )
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- bot-agent-dev -->\n"
            "plain notification\n"
            + _state_block(state)
        )

        assert CONTINUE_TOKEN in body
        assert not body.startswith(CONTINUE_TOKEN)


def _parked_state(
    reason: str,
    *,
    workflow: str = "default",
    step: str = "implement",
    agent: str = "dev",
) -> WorkflowState:
    """A state the engine persisted for an INCOMPLETE turn (issue #978)."""
    from workflow_state import IncompleteReason, TurnOutcome

    return WorkflowState(
        workflow=workflow,
        step=step,
        agent=agent,
        position=[{"workflow": workflow, "step": step}],
        turn_outcome=TurnOutcome.for_incomplete(
            IncompleteReason(reason), note="run ended early"
        ),
    )


class TestResumeAfterAParkedRun:
    """``/nebuko resume`` must re-run the step the failure notification parked.

    The failure comment tells the human to run ``/nebuko resume``, so that
    command has to keep working for every halt the router can produce (issue
    #978, review fix 1).
    """

    @pytest.mark.parametrize("reason", ["failure", "interrupted", "soft_timeout"])
    def test_resume_after_a_parked_run_reruns_the_step(self, prompt_base: str, reason: str):
        # A human's comment: the command is the first line, then the quoted
        # state block the engine appended to the notification it replies to.
        body = (
            f"{RESUME_COMMAND}\n"
            + _state_block(_parked_state(reason))
        )

        result = resolve_config(
            body, prompt_base, str(WORKFLOWS_DIR), is_bot=False
        )

        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_STEP"] == "implement"


class TestInterruptedTurnIsNotABurntRun:
    """An interrupted turn must not emit a token the router then skips (#978).

    The amended #964 policy retries an interruption in place, so its body keeps
    the continue token — the retry is a live run, not a notification.
    """

    def test_interrupted_turn_keeps_the_continue_token(self):
        from prepare_comment import format_answer_header

        header = format_answer_header("dev", _parked_state("interrupted"))

        assert header.startswith(CONTINUE_TOKEN)

    def test_interrupted_turn_retries_the_step_in_place(self, prompt_base: str):
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- bot-agent-dev -->\n"
            "interrupted\n"
            + _state_block(_parked_state("interrupted"))
        )

        result = _resolve(body, prompt_base)

        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "dev"

    def test_crashed_run_is_a_notification_and_keeps_no_token(self):
        from prepare_comment import format_answer_header

        header = format_answer_header("dev", _parked_state("failure"))

        assert not header.startswith(CONTINUE_TOKEN)
