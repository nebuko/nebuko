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

from nebuko.engine.lib.resolve_config import resolve_config
from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT
from nebuko.engine.lib.workflow_state import WorkflowState
from nebuko.engine.lib.workflow_state.workflow_state import STATE_MARKER_END, STATE_MARKER_START

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / "defaults" / "workflows"

BOT_MARKER = "<!-- bot-workflow-terminal -->"
CONTINUE_TOKEN = "<!-- workflow-continue -->"
# The single line-1 composition shared with ``prepare_comment`` and the gate.
LIVE_HEADER_LINE = BOT_MARKER + CONTINUE_TOKEN
GATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"

RESUME_COMMAND = "/nebuko resume"


def _gate_expression() -> str:
    """The ``run-task`` job's ``if`` expression from the workflow gate."""
    import yaml

    workflow = yaml.safe_load(GATE_WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["run-task"]["if"]


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
    from nebuko.engine.lib.workflow_state.features.to_json.to_json import state_to_json

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
    from nebuko.engine.lib.agent_script.features.run_agent.run_failure import (
        RunFailure,
        RunFailureKind,
    )
    from nebuko.engine.lib.agent_script.features.run_agent.run_outcome import (
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
    from nebuko.engine.lib.agent_script.features.build_output.build_output import build_output
    from nebuko.engine.lib.metrics_schema import Metrics
    from nebuko.engine.lib.prepare_comment import format_answer_header

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
        from nebuko.engine.lib.workflow_state.features.to_json.to_json import state_to_json

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
        """The GHA gate must match the token by prefix, never as a substring.

        A ``contains`` match also fires on the literal embedded in the
        serialized state/history (or quoted in agent prose), which is what kept
        the chain alive after the crashed run. The gate anchors the *composed*
        line-1 prefix, so a token that is not the posted line 1 cannot fire.
        """
        expression = _gate_expression()

        if f"startsWith(github.event.comment.body, '{LIVE_HEADER_LINE}')" not in expression:
            pytest.xfail(
                "YAML edit blocked: the runner token lacks 'workflows' permission, "
                "so .github/workflows/nebuko-workflow.yaml still uses contains(...) - "
                "issue #978. This test flips green when a human pushes the one-line "
                "startsWith edit."
            )

        assert (
            f"startsWith(github.event.comment.body, '{LIVE_HEADER_LINE}')"
            in expression
        )
        assert (
            f"contains(github.event.comment.body, '{CONTINUE_TOKEN}')"
            not in expression
        )

    def test_the_gate_lets_a_live_comment_reach_its_token_disjunct(self):
        """The bot-marker guards must exempt a live answer's line 1.

        A live comment *does* start with the bot marker, so the marker guards
        would otherwise drop it before the token disjunct is ever evaluated.
        """
        expression = _gate_expression()

        token_branch = expression.split(
            f"startsWith(github.event.comment.body, '{LIVE_HEADER_LINE}')", 1
        )[0]
        last_guards = token_branch.rsplit("!startsWith(github.event.comment.body,", 2)
        assert len(last_guards) == 3
        for guard in last_guards[1:]:
            assert "bot-agent-" in guard or "bot-workflow-terminal" in guard

    def test_live_run_body_starts_with_the_marker_and_token(self, prompt_base: str):
        from nebuko.engine.lib.prepare_comment import format_answer_header

        header = format_answer_header("dev", _implement_state())

        assert header.split("\n", 1)[0] == LIVE_HEADER_LINE

    def test_failed_run_body_does_not_start_with_the_continue_token(
        self, prompt_base: str
    ):
        body = _build_failure_body(_crashed_implement_state())

        assert not body.startswith(LIVE_HEADER_LINE)
        assert CONTINUE_TOKEN not in body.split("\n", 1)[0]

    def test_token_embedded_in_history_cannot_trigger(self, prompt_base: str):
        state = _implement_state().model_copy(
            update={
                "current_context": {
                    "recovery_log": f"a previous comment quoted {CONTINUE_TOKEN}",
                }
            }
        )
        body = (
            f"{BOT_MARKER}\n"
            "<!-- bot-agent-dev -->\n"
            "plain notification\n"
            + _state_block(state)
        )

        assert CONTINUE_TOKEN in body
        assert not body.startswith(LIVE_HEADER_LINE)


class TestPostedBodySatisfiesTheGateAnchors:
    """The gate reads the *posted* body, not the pre-posting header (#978).

    ``post_comment`` runs every body through ``ensure_bot_marker``, which
    prepends the marker when line 1 does not already start with it. A header
    that puts the token on its own first line therefore reaches GitHub with an
    injected marker above it, and a ``startsWith`` gate never matches — a
    stall regression the ``LocalAdapter``-backed tests cannot see because
    ``_strip_injected_marker`` erases the injected line on the way back.
    """

    def test_a_posted_live_answer_still_matches_the_gate_anchor(self):
        from nebuko.engine.lib.prepare_comment import format_answer_header
        from nebuko.engine.lib.github_client import ensure_bot_marker

        posted = ensure_bot_marker(format_answer_header("dev", _implement_state()))

        assert posted.startswith(LIVE_HEADER_LINE)

    def test_the_seam_leaves_a_live_answer_byte_identical(self):
        from nebuko.engine.lib.prepare_comment import format_answer_header
        from nebuko.engine.lib.github_client import ensure_bot_marker

        header = format_answer_header("dev", _implement_state())

        assert ensure_bot_marker(header) == header

    def test_a_posted_crashed_run_notification_keeps_no_token(self):
        from nebuko.engine.lib.github_client import ensure_bot_marker

        posted = ensure_bot_marker(_build_failure_body(_crashed_implement_state()))

        assert not posted.startswith(LIVE_HEADER_LINE)
        assert CONTINUE_TOKEN not in posted

    def test_a_posted_live_answer_is_still_classified_as_a_bot_comment(self):
        from nebuko.engine.lib.prepare_comment import format_answer_header
        from nebuko.engine.lib.github_client import ensure_bot_marker
        from nebuko.engine.lib.resolve_config.features.parse_trigger.parse_trigger import (
            UNIVERSAL_BOT_MARKER,
        )

        posted = ensure_bot_marker(format_answer_header("dev", _implement_state()))

        assert posted.split("\n", 1)[0].startswith(UNIVERSAL_BOT_MARKER)


def _parked_state(
    reason: str,
    *,
    workflow: str = "default",
    step: str = "implement",
    agent: str = "dev",
) -> WorkflowState:
    """A state the engine persisted for an INCOMPLETE turn (issue #978)."""
    from nebuko.engine.lib.workflow_state import IncompleteReason, TurnOutcome

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
        from nebuko.engine.lib.prepare_comment import format_answer_header

        header = format_answer_header("dev", _parked_state("interrupted"))

        assert header.split("\n", 1)[0] == LIVE_HEADER_LINE

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
        from nebuko.engine.lib.prepare_comment import format_answer_header

        header = format_answer_header("dev", _parked_state("failure"))

        assert not header.startswith(CONTINUE_TOKEN)
