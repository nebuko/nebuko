"""Spec-scenario tests for the early workflow-state comment (issue #727).

The Stage B cutover dropped the legacy comment-assembly step
(``legacy-flow.yaml:865-927``), so posted comments no longer carry the
``WORKFLOW_STATE_START/END`` block. ``CommentComposer`` restores that body — an
early, pre-agent notice — with the bot marker on line 1 (issue #478), the caller
marker, a deterministic continue token when the flow stays alive, the collapsed
state section, and the role announcement.
"""

from __future__ import annotations

from engine.comment_composer import CommentComposer
from workflow_state.sub_domains.dispatch.dispatch import Dispatch
from workflow_state.workflow_state import (
    STATE_MARKER_END,
    STATE_MARKER_START,
    WorkflowState,
)

BOT_MARKER = "<!-- bot-workflow-terminal -->"
CALLER_MARKER = "<!-- bot-agent-dev -->"
CONTINUE_TOKEN = "<!-- workflow-continue -->\n"


def _state(status: str | None = "advance") -> WorkflowState:
    """A minimal run-start state; ``status=None`` models an absent dispatch."""
    return WorkflowState(
        version=7,
        workflow="spec-driven-impl",
        step="stub",
        agent="dev",
        dispatch=None if status is None else Dispatch(status=status),
    )


def _body(status: str | None = "advance", *, display_name: str = "Developer") -> str:
    return CommentComposer().compose_run_start(
        _state(status), caller_marker=CALLER_MARKER, display_name=display_name
    )


class TestContinueToken:
    def test_emits_the_token_for_an_advancing_run(self):
        assert CommentComposer().continue_token(_state("advance")) == CONTINUE_TOKEN

    def test_emits_the_token_for_a_looping_run(self):
        assert CommentComposer().continue_token(_state("loop")) == CONTINUE_TOKEN

    def test_emits_the_token_when_there_is_no_dispatch(self):
        assert CommentComposer().continue_token(_state(None)) == CONTINUE_TOKEN

    def test_omits_the_token_for_a_hold(self):
        assert CommentComposer().continue_token(_state("hold")) == ""

    def test_omits_the_token_for_a_terminate(self):
        assert CommentComposer().continue_token(_state("terminate")) == ""

    def test_omits_the_token_for_a_soft_kill(self):
        assert CommentComposer().continue_token(_state("soft_kill")) == ""


class TestComposeRunStart:
    def test_puts_the_bot_marker_on_the_first_line(self):
        assert _body().splitlines()[0] == BOT_MARKER

    def test_embeds_the_collapsed_state_section(self):
        body = _body()

        assert STATE_MARKER_START in body
        assert STATE_MARKER_END in body
        assert '"step": "stub"' in body

    def test_orders_marker_token_state_and_role_line(self):
        body = _body()

        assert CALLER_MARKER in body
        assert "*Developer processing*" in body
        assert (
            body.index(CALLER_MARKER)
            < body.index(CONTINUE_TOKEN)
            < body.index(STATE_MARKER_START)
            < body.index("*Developer processing*")
        )

    def test_omits_the_token_when_the_flow_ends(self):
        assert CONTINUE_TOKEN not in _body("hold")

    def test_injects_the_terminal_marker(self):
        body = CommentComposer("<!-- custom-marker -->").compose_run_start(
            _state(), caller_marker=CALLER_MARKER, display_name="Developer"
        )

        assert body.splitlines()[0] == "<!-- custom-marker -->"
