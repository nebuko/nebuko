"""Spec-scenario tests for the workflow-state answer comment (issue #736).

The Stage B cutover dropped the legacy comment-assembly step
(``legacy-flow.yaml:877-960``), so posted comments no longer carry the
``WORKFLOW_STATE_START/END`` block. ``CommentComposer`` restores that body on
the *agent-answer* comment — with the bot marker on line 1 (issue #478), the
caller marker, a deterministic continue token when the flow stays alive, the
collapsed state section, the role answer line and finally the answer text.
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
ANSWER = "Implemented the step."


def _state(status: str | None = "advance") -> WorkflowState:
    """A minimal agent state; ``status=None`` models an absent dispatch."""
    return WorkflowState(
        version=7,
        workflow="spec-driven-impl",
        step="stub",
        agent="dev",
        dispatch=None if status is None else Dispatch(status=status),
    )


def _body(
    status: str | None = "advance",
    *,
    display_name: str = "Developer",
    state: WorkflowState | None | object = ...,
) -> str:
    resolved = _state(status) if state is ... else state
    return CommentComposer().compose_answer(
        resolved, caller_marker=CALLER_MARKER, display_name=display_name, body=ANSWER
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


class TestComposeAnswer:
    def test_puts_the_bot_marker_on_the_first_line(self):
        assert _body().splitlines()[0] == BOT_MARKER

    def test_embeds_the_collapsed_state_section(self):
        body = _body()

        assert STATE_MARKER_START in body
        assert STATE_MARKER_END in body
        assert '"step": "stub"' in body

    def test_orders_marker_token_state_role_line_and_answer(self):
        body = _body()

        assert CALLER_MARKER in body
        assert "*Developer answer*" in body
        assert (
            body.index(CALLER_MARKER)
            < body.index(CONTINUE_TOKEN)
            < body.index(STATE_MARKER_START)
            < body.index("*Developer answer*")
            < body.index(ANSWER)
        )

    def test_omits_the_token_when_the_flow_ends(self):
        assert CONTINUE_TOKEN not in _body("hold")

    def test_injects_the_terminal_marker(self):
        body = CommentComposer("<!-- custom-marker -->").compose_answer(
            _state(), caller_marker=CALLER_MARKER, display_name="Developer", body=ANSWER
        )

        assert body.splitlines()[0] == "<!-- custom-marker -->"

    def test_keeps_the_header_but_omits_the_section_without_a_state(self):
        body = _body(state=None)

        assert body.splitlines()[0] == BOT_MARKER
        assert CALLER_MARKER in body
        assert CONTINUE_TOKEN in body
        assert STATE_MARKER_START not in body
        assert STATE_MARKER_END not in body
        assert ANSWER in body
