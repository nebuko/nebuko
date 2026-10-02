"""Compose the early, pre-agent workflow-state comment (issue #727).

The Stage B cutover dropped the legacy comment-assembly step
(``.github/workflows/legacy-flow.yaml:865-927``): comments are now posted by
the engine, and the ``WORKFLOW_STATE_START/END`` block never reached them, so
every run stopped carrying its durable state. This module owns the shape of the
*early* comment — the notice posted after the step/agent decision is resolved
and before the agent runs — so that transport is restored in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from workflow_state.workflow_state import WorkflowState

__all__ = ["CommentComposer"]


class CommentComposer:
    """Compose the early, pre-agent workflow-state comment body.

    Single owner of the ordering/format invariants the legacy GHA step
    assembled inline (``legacy-flow.yaml:865-927``): the bot marker on line 1
    (issue #478), then the caller marker, the optional continue token
    (``advance``/``loop`` only), the collapsed workflow-state section, and the
    role announcement line.
    """

    BOT_MARKER: str = "<!-- bot-workflow-terminal -->"

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, bot_marker: str = BOT_MARKER) -> None:
        """Inject the line-1 terminal marker (test seam); default ``BOT_MARKER``."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def continue_token(self, state: "WorkflowState") -> str:
        """Return the deterministic continuation token for ``state``.

        Non-empty (``"<!-- workflow-continue -->\\n"``) when ``state.dispatch``
        is absent or its status is ``advance``/``loop``; empty for every other
        status, so a ``hold``/``terminate`` ends the flow (issue #478).
        """
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def compose_run_start(
        self,
        state: "WorkflowState",
        *,
        caller_marker: str,
        display_name: str,
    ) -> str:
        """Build the early state-comment body for a run start.

        The body puts :attr:`BOT_MARKER` on line 1, then ``caller_marker``, the
        :meth:`continue_token` output, the collapsed state section
        (``format_collapsed_section``), and finally the role announcement line
        ``*<display_name> processing*``.
        """
        raise NotImplementedError
