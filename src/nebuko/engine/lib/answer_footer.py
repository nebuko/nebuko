"""One-line workflow-status footer appended to every agent answer (#1159).

A human juggling several parallel issues needs to see, on each agent answer,
which workflow is running, where it stands and what to do next. The footer is
engine-rendered at publish time from the same ``WorkflowState`` that already
rides the collapsed state block; agents never emit it (agents are untrusted
narrators — issue #482 is why engine-rendered, escaped text is the reliable
channel).

Format: ``{workflow} | {steps} | {status} | {hint}`` — e.g.::

    spec-tdd | plan -> **spec** -> implement | gate: need approval | approve or reject: /nebuko approve|reject gate

The footer is appended inside ``build_comment_body_parts`` (prepare_comment), so
it rides inside the body before trigger-escaping and multi-part splitting; the
``/nebuko`` tokens in hints are inert text (commands are classified by author,
never by content). Footers are append-only snapshots: each answer keeps the
footer of its moment, and the latest agent answer is the authoritative status.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
    from workflow_state.sub_domains.thread_record.thread_record import ThreadRecord
    from workflow_state.workflow_state import WorkflowState


class FooterStatus(str, Enum):
    """The human-readable status shown in the footer's third segment."""

    IN_PROGRESS = "in progress"
    GATE_ARMED = "gate: need approval"
    AWAITING_INPUT = "waiting for your input"
    SOFT_RETRY_EXHAUSTED = "soft-kill failed"
    CIRCUIT_OPEN = "circuit breaker open"


class StepLinkResolver:
    """Maps a workflow step name to its latest agent-output permalink.

    Resolution path: ``HistoryEntry(step).comment_ids`` -> the latest matching
    non-deleted ``ThreadRecord`` with ``kind == ThreadRecordKind.AGENT_OUTPUT``
    and a non-empty ``url``. Steps with no posted output resolve to ``""``.
    """

    def __init__(
        self,
        thread: Sequence["ThreadRecord"] = (),
        history: Sequence["HistoryEntry"] = (),
    ) -> None:
        self._thread: tuple = tuple(thread)
        self._history: tuple = tuple(history)

    # @stub-protected
    def url_for(self, step: str) -> str:
        """Return the permalink of ``step``'s latest agent output, or ''."""
        raise NotImplementedError("StepLinkResolver.url_for stub")


class AnswerFooter:
    """Renders the one-line workflow footer appended to agent answers."""

    def __init__(self, resolver: Optional[StepLinkResolver] = None) -> None:
        self.resolver = resolver

    # @stub-protected
    def render(
        self, state: "WorkflowState", steps: Optional[Sequence[str]] = None
    ) -> str:
        """Render ``{workflow} | {steps} | {status} | {hint}``.

        Returns ``""`` when the workflow name is empty. The hint segment
        (with its pipe) is omitted entirely when the hint is empty.
        """
        raise NotImplementedError("AnswerFooter.render stub")

    # @stub-protected
    def render_steps(
        self, state: "WorkflowState", steps: Optional[Sequence[str]]
    ) -> str:
        """Render the steps segment: linked completed, bold current, plain pending."""
        raise NotImplementedError("AnswerFooter.render_steps stub")

    # @stub-protected
    def resolve_status(self, state: "WorkflowState") -> FooterStatus:
        """First match: circuit open, soft retries exhausted, gate armed,
        awaiting-input hold, else in progress."""
        raise NotImplementedError("AnswerFooter.resolve_status stub")

    # @stub-protected
    def render_hint(self, status: FooterStatus, state: "WorkflowState") -> str:
        """The fixed next-action hint for ``status`` ('' for in-progress)."""
        raise NotImplementedError("AnswerFooter.render_hint stub")


def format_answer_footer(
    state: Optional["WorkflowState"], steps: Optional[Sequence[str]] = None
) -> str:
    """Module seam used by ``prepare_comment.build_comment_body_parts``."""
    raise NotImplementedError("format_answer_footer stub")
