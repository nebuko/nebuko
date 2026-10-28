"""One-line workflow-status footer appended to every agent answer (issue #1159).

A human returning to an issue running several workflows in parallel needs the
"where are we / what next" facts at a glance. The engine already carries them
in the ``WorkflowState`` that rides every agent answer's collapsed header
block; this module renders them as a single pipe-separated footer line:

    ``{workflow} | {steps} | {status} | {hint}``

e.g.::

    spec-tdd | plan -> **spec** -> implement | gate: need approval | approve or reject: /nebuko approve|reject gate

Pure rendering only: the publish seam (``prepare_comment.build_comment_body_parts``)
appends the footer to the answer body *before* ``escape_agent_triggers`` and
multi-part splitting, so the footer can never re-arm a gate (issue #482) and
lands on the last part of a split answer. Script-step comments and engine
output are excluded — only agent answers carry the footer.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only (issue #590)
    from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
    from workflow_state.sub_domains.thread_record.thread_record import ThreadRecord
    from workflow_state.workflow_state import WorkflowState

__all__ = [
    "FooterStatus",
    "StepLinkResolver",
    "AnswerFooter",
    "format_answer_footer",
]


class FooterStatus(str, Enum):
    """The human-readable run status shown in the footer's third segment."""

    IN_PROGRESS = "in progress"
    GATE_ARMED = "gate: need approval"
    AWAITING_INPUT = "waiting for your input"
    SOFT_RETRY_EXHAUSTED = "soft-kill failed"
    CIRCUIT_OPEN = "circuit breaker open"


class StepLinkResolver:
    """Maps a workflow step name to the permalink of its latest agent answer.

    Resolution path: the step's ``HistoryEntry.comment_ids`` → the latest
    matching non-deleted ``ThreadRecord(kind=AGENT_OUTPUT)`` → its ``url``.
    Steps with no posted output resolve to ``""`` so the renderer falls back
    to the plain step name.
    """

    def __init__(
        self,
        thread: Sequence[ThreadRecord] = (),
        history: Sequence[HistoryEntry] = (),
    ) -> None:
        self._thread: tuple[ThreadRecord, ...] = tuple(thread)
        self._history: tuple[HistoryEntry, ...] = tuple(history)

    # @stub-protected: signature defined by architect — do not modify
    def url_for(self, step: str) -> str:
        """Return the permalink of ``step``'s latest agent answer, or ``""``."""
        raise NotImplementedError


class AnswerFooter:
    """Renders the one-line workflow footer appended to agent answers."""

    def __init__(self, resolver: StepLinkResolver | None = None) -> None:
        self.resolver = resolver

    # @stub-protected: signature defined by architect — do not modify
    def render(
        self,
        state: WorkflowState,
        steps: Sequence[str] | None = None,
    ) -> str:
        """Render the full footer, or ``""`` when there is nothing to show.

        ``""`` when ``state is None`` or ``state.workflow`` is empty;
        otherwise ``"{workflow} | {steps} | {status} | {hint}"``. The hint
        segment (and the separator before it) is omitted entirely for
        ``FooterStatus.IN_PROGRESS``.
        """
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def render_steps(
        self,
        state: WorkflowState,
        steps: Sequence[str] | None,
    ) -> str:
        """Render the steps segment, e.g. ``plan -> [spec](url) -> **implement**``.

        ``steps`` is the full ordered step list when the engine can supply the
        workflow definition's step names; otherwise it is derived from
        ``state.history`` plus the current step. Each completed step is a
        Markdown link to its latest output comment permalink (plain step name
        when ``resolver`` is ``None`` or ``url_for`` returns ``""``); the
        current step is bold; pending steps are plain. Separator: ``" -> "``.
        """
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def resolve_status(self, state: WorkflowState) -> FooterStatus:
        """Resolve the run status; first match wins.

        Order: circuit breaker open → soft-retry budget exhausted → armed
        human gate (``state.gate.armed()``) → awaiting-input hold
        (``turn_outcome`` appreciation ``hold``) → :attr:`FooterStatus.IN_PROGRESS`.
        """
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def render_hint(self, status: FooterStatus, state: WorkflowState) -> str:
        """The next-action hint for ``status``.

        Mapping:
            GATE_ARMED:           ``approve or reject: /nebuko approve|reject gate``
            AWAITING_INPUT:       ``reply then /nebuko resume``
            SOFT_RETRY_EXHAUSTED: ``retry /nebuko resume or escalate to architect``
            CIRCUIT_OPEN:         ``adjust: /nebuko update circuit-breaker --value <n>``
            IN_PROGRESS:          ``""`` (hint segment omitted entirely)
        """
        raise NotImplementedError


# @stub-protected: signature defined by architect — do not modify
def format_answer_footer(
    state: WorkflowState | None,
    steps: Sequence[str] | None = None,
) -> str:
    """Module-level seam used by the publish path.

    ``""`` when ``state`` is ``None``; otherwise ``AnswerFooter().render(state, steps)``.
    """
    raise NotImplementedError
