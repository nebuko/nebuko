"""Engine-owned capture of the agent's substantive answer (issue #740).

The engine posts the agent's *last text answer* as the issue comment, but the
terminal control turns (``finish`` / ``dispatch_next``) also carry turn text —
a short status/routing note. A latest-wins scan over every turn therefore lets
that note clobber the real deliverable (the #738 repro). :class:`AnswerCapture`
observes the run's turn stream in order and *freezes* the last substantive
answer at the first end-tool boundary, so the terminal note can never overwrite
it (Option B: capture the answer before the end tools run).

The observer is written to the OpenHands SDK callback contract
(``Conversation(callbacks=[capture.on_event])``), so the engine registers it
directly once it owns the conversation loop; the in-process run seam feeds the
same ordered events through the identical callback until then.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DEFAULT_END_TOOLS", "AnswerCapture", "AnswerSnapshot"]

# The two control-plane turns that terminate the agent's answer. Their turn
# text is a status note (``finish``) or routing metadata (``dispatch_next``),
# never the deliverable — see ``skills/posting-contract.md`` rule 2.
DEFAULT_END_TOOLS = frozenset({"finish", "dispatch_next"})


@dataclass(frozen=True)
class AnswerSnapshot:
    """The substantive agent answer frozen at the end-tool boundary.

    Attributes:
        text: The deliverable registered just before the end tool ran.
        event_index: Zero-based position of the end-tool turn in the stream.
        end_tool: The ``finish`` / ``dispatch_next`` turn that froze the answer.
    """

    text: str
    event_index: int
    end_tool: str


class AnswerCapture:
    """Register the agent's substantive answer, then freeze it before end tools.

    Each event is classified by turn type:

    * an agent ``MessageEvent`` (a standalone text turn) registers/replaces the
      current substantive answer;
    * a ``finish`` / ``dispatch_next`` ``ActionEvent`` is the end-tool boundary —
      it freezes the registered answer and its own turn text is **never**
      adopted;
    * every other event (user turns, tool-call turns) is ignored.

    The **first** end-tool trigger wins, so an engine-injected recall turn can
    never clobber a pre-recall deliverable (subsumes the #558 offset).
    """

    def __init__(self, end_tools: frozenset[str] = DEFAULT_END_TOOLS) -> None:
        self._end_tools = frozenset(end_tools)
        self._current = ""
        self._snapshot: AnswerSnapshot | None = None
        self._seen = 0

    def on_event(self, event: object) -> None:
        """Observe one conversation event, in stream order.

        Safe to use as an SDK ``Conversation`` callback — the SDK invokes it
        with every event *before* the corresponding action executes.
        """
        index = self._seen
        self._seen += 1
        if self._snapshot is not None:  # frozen: later turns cannot clobber it
            return

        end_tool = _end_tool_of(event, self._end_tools)
        if end_tool is not None:
            self._snapshot = AnswerSnapshot(
                text=self._current, event_index=index, end_tool=end_tool
            )
            return

        text = _message_text(event)
        if text:
            self._current = text

    @property
    def snapshot(self) -> AnswerSnapshot | None:
        """The answer frozen at the end-tool boundary, or ``None`` if none ran."""
        return self._snapshot

    def deliverable(self, end_tool_answer: str = "") -> str:
        """Return the captured deliverable.

        ``end_tool_answer`` (the legacy ``latest_answer.md``) is used **only**
        when nothing substantive was registered — the last-resort merge that
        keeps a run whose whole answer lived on the end-tool turn (the #555
        shared-turn case) working.
        """
        if self._snapshot is not None and self._snapshot.text:
            return self._snapshot.text
        if self._current:
            return self._current
        return end_tool_answer


def _end_tool_of(event: object, end_tools: frozenset[str]) -> str | None:
    """The end-tool name when ``event`` is a control-plane turn, else ``None``."""
    tool_name = getattr(event, "tool_name", None)
    return tool_name if tool_name in end_tools else None


def _message_text(event: object) -> str:
    """The standalone agent text of a ``MessageEvent``; empty for any action turn."""
    if getattr(event, "tool_name", None) is not None:
        return ""  # tool-call turn: its text is not a standalone deliverable
    if getattr(event, "source", None) != "agent":
        return ""
    message = getattr(event, "llm_message", None)
    if message is None:
        return ""
    return _content_text(getattr(message, "content", None)).strip()


def _content_text(content: object) -> str:
    """Join an SDK message content value into plain text.

    Mirrors ``openhands.sdk.llm.message.content_to_str`` without importing the
    SDK, so this leaf module stays importable in a platform-free context.
    """
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for item in content:  # type: ignore[union-attr]
        text = getattr(item, "text", None)
        parts.append(text if isinstance(text, str) else str(item))
    return "".join(parts)
