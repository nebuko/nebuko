"""Compose the agent-answer comment with its workflow state (issue #736).

The Stage B cutover dropped the legacy comment-assembly step
(``.github/workflows/legacy-flow.yaml:877-960``), so comments posted by the
engine no longer carried the ``WORKFLOW_STATE_START/END`` block. A first
restoration (issue #727) posted that block as a separate, pre-agent notice —
the wrong position: the state belongs on the *answer* comment, which is also
the run's authoritative output (its ``dispatch`` drives the next step). This
module owns the shape of the answer header so transport lives in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.paths import ensure_import_path

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from workflow_state.workflow_state import WorkflowState

__all__ = ["CommentComposer", "CONTINUE_TOKEN"]

# The deterministic continuation token (issue #478): the next CI run fires on
# this hidden marker instead of relying on stray ``@@`` tokens in agent prose.
CONTINUE_TOKEN = "<!-- workflow-continue -->\n"

# The dispatch statuses that keep the flow alive and therefore carry the token.
_LIVE_STATUSES = ("advance", "loop")


class CommentComposer:
    """Compose the agent-answer comment body that carries the workflow state.

    Single owner of the ordering/format invariants the legacy GHA step
    assembled inline (``legacy-flow.yaml:877-960``): the bot marker on line 1
    (issue #478), then the caller marker, the optional continue token
    (``advance``/``loop`` only), the collapsed workflow-state section, the
    ``*<role> answer*`` line and finally the answer text.
    """

    BOT_MARKER: str = "<!-- bot-workflow-terminal -->"

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, bot_marker: str = BOT_MARKER) -> None:
        """Inject the line-1 terminal marker (test seam); default ``BOT_MARKER``."""
        self.bot_marker = bot_marker

    # @stub-protected: signature defined by architect — do not modify
    def continue_token(self, state: "WorkflowState") -> str:
        """Return the deterministic continuation token for ``state``.

        Non-empty (``"<!-- workflow-continue -->\\n"``) when ``state.dispatch``
        is absent or its status is ``advance``/``loop``; empty for every other
        status, so a ``hold``/``terminate`` ends the flow (issue #478).
        """
        status = state.dispatch.status if state.dispatch else "advance"
        return CONTINUE_TOKEN if status in _LIVE_STATUSES else ""

    # @stub-protected: signature defined by architect — do not modify
    # (issue #736 supersedes the #727 ``compose_run_start`` placement: the
    # header now frames the answer and takes the answer ``body``.)
    def compose_answer(
        self,
        state: "WorkflowState | None",
        *,
        caller_marker: str,
        display_name: str,
        body: str,
    ) -> str:
        """Frame an agent answer with the workflow-state header (issue #736).

        Mirrors the legacy "Add bot marker and workflow state to comment" step
        (``legacy-flow.yaml:877-960``): :attr:`BOT_MARKER` on line 1, then
        ``caller_marker``, the :meth:`continue_token` output, the collapsed
        state section (``format_collapsed_section``), the
        ``*<display_name> answer*`` line, and finally the answer ``body``.

        A ``None`` state — the agent emitted no ``workflow_state.json``, so the
        run's output is not authoritative — keeps the marker, caller and token
        but omits the section, exactly like the legacy step (which defaulted
        the token on and skipped only the section).
        """
        section = ""
        if state is not None:
            ensure_import_path()
            from workflow_state.features.comment.to_git_comment import (
                format_collapsed_section,
            )

            section = format_collapsed_section(state)
        token = CONTINUE_TOKEN if state is None else self.continue_token(state)
        return (
            f"{self.bot_marker}\n"
            f"{caller_marker}\n"
            f"{token}"
            f"{section}"
            f"\n*{display_name} answer*\n\n"
            f"{body}"
        )
