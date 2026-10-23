"""Engine-owned capture of the agent's substantive answer (issues #740, #830).

The engine posts the agent's deliverable as the issue comment. Two sources are
considered, in a fixed, deterministic order:

1. **The typed engine-tool payload** — a ``finish`` action's ``message``
   argument, harvested structurally through
   :data:`~nebuko.engine.engine_tools.DEFAULT_ENGINE_TOOLS`. The engine-elicited
   report turn fills this slot, so the deliverable can never depend on where an
   agent happened to place its prose (the #822 truncation bug).
2. **The last standalone agent message** registered before the terminals turn,
   frozen at the first end-tool boundary so that terminal note cannot clobber
   it (the #738 repro, Option B of #740).

:class:`AnswerSnapshot` records which source produced the deliverable so a
degraded (heuristic) path is *marked*, never guessed.

The observer is written to the OpenHands SDK callback contract
(``Conversation(callbacks=[capture.on_event])``), so the engine registers it
directly once it owns the conversation loop; the in-process run seam feeds the
same ordered events through the identical callback until then.
"""

from __future__ import annotations

from dataclasses import dataclass

from nebuko.engine.engine_tools import (
    DEFAULT_ENGINE_TOOLS,
    TERMINAL_PAYLOAD_KINDS,
    EngineToolPayload,
    EngineToolRegistry,
)

__all__ = ["DEFAULT_END_TOOLS", "AnswerCapture", "AnswerSnapshot"]

# The control-plane turns that end the work loop, derived from the engine-tool
# registry's terminal payload kinds (one classification, no parallel list to
# drift — issue #748). A ``query`` tool returns data to the agent and must not
# freeze the answer, so only terminal kinds are boundaries. Their turn text is a
# status note (``finish``) or routing metadata (``dispatch_next``); the
# deliverable is the tool's *typed* payload, not its prose.
DEFAULT_END_TOOLS = frozenset(
    name
    for name, spec in DEFAULT_ENGINE_TOOLS.tools.items()
    if spec.payload_kind in TERMINAL_PAYLOAD_KINDS
)


@dataclass(frozen=True)
class AnswerSnapshot:
    """The deliverable frozen at the end-tool boundary.

    Attributes:
        text: The deliverable frozen at the boundary — the typed engine-tool
            payload when present, else the last standalone agent message, else
            the boundary turn's own thought (the #555 case).
        event_index: Zero-based position of the boundary turn in the stream.
        end_tool: The ``finish`` / ``dispatch_next`` turn that froze the answer.
        source: Which source produced ``text`` — ``"report_finish"`` (the
            engine-elicited report turn), ``"work_loop_finish"`` (a typed
            work-loop engine-tool message), ``"last_message"`` or ``"thought"``
            (the heuristic fallbacks). Abnormal paths are visibly marked.
    """

    text: str
    event_index: int
    end_tool: str
    source: str = "last_message"


class AnswerCapture:
    """Register the agent's substantive answer, then freeze it before end tools.

    Each event is classified by turn type:

    * a ``finish`` / ``dispatch_next`` ``ActionEvent`` is the end-tool boundary.
      A **typed** engine-tool payload (``finish.message``) is the canonical
      deliverable and supersedes any earlier freeze — this is where the
      engine-elicited report turn lands. Without a typed payload the first
      boundary freezes the last standalone message (or, when the deliverable
      shared the boundary turn, its own thought — the #555 case); later control
      turns cannot clobber it;
    * an agent ``MessageEvent`` (a standalone text turn) registers/replaces the
      current substantive answer;
    * every other event (user turns, tool-call turns) is ignored.
    """

    def __init__(
        self,
        end_tools: frozenset[str] | None = None,
        registry: EngineToolRegistry = DEFAULT_ENGINE_TOOLS,
    ) -> None:
        self._registry = registry
        self._end_tools = (
            frozenset(registry.tools) if end_tools is None else frozenset(end_tools)
        )
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

        tool_name = getattr(event, "tool_name", None)
        if tool_name in self._end_tools:
            self._freeze_from_engine_tool(event, tool_name, index)
            return

        if self._snapshot is not None:  # frozen: later turns cannot clobber it
            return
        text = _message_text(event)
        if text:
            self._current = text

    def _freeze_from_engine_tool(
        self, event: object, tool_name: str, index: int
    ) -> None:
        # @stub-protected: signature defined by architect — do not modify
        """Freeze/merge from an engine-tool action's typed payload.

        The first boundary freezes the deliverable. A standalone text answer
        is the documented deliverable and wins; with no standalone answer, a
        typed ``finish.message`` is canonical (the #822 case), falling back to
        the boundary turn's own thought (the #555 case).
        """
        payload = self._registry.harvest(tool_name, getattr(event, "action", None))
        typed = _final_message_text(payload)
        if typed:
            if self._current:
                text, source = self._current, "last_message"
            else:
                text, source = typed, "work_loop_finish"
            self._snapshot = AnswerSnapshot(
                text=text, event_index=index, end_tool=tool_name, source=source
            )
            return
        if self._snapshot is not None:  # a later note cannot clobber the freeze
            return
        self._snapshot = AnswerSnapshot(
            text=self._current or _action_thought_text(event),
            event_index=index,
            end_tool=tool_name,
            source="last_message" if self._current else "thought",
        )

    def freeze_report_payload(self, payload: EngineToolPayload) -> None:
        # @stub-protected: signature defined by architect — do not modify
        """Canonical entry: freeze the deliverable from the report turn payload.

        The engine-elicited report turn's typed payload is authoritative — it
        replaces any work-loop freeze, because the engine owns the slot.
        """
        text = _final_message_text(payload)
        if not text:
            return
        self._snapshot = AnswerSnapshot(
            text=text,
            event_index=self._seen,
            end_tool=payload.tool_name,
            source="report_finish",
        )

    @property
    def snapshot(self) -> AnswerSnapshot | None:
        """The answer frozen at the end-tool boundary, or ``None`` if none ran."""
        return self._snapshot

    def deliverable(self, end_tool_answer: str = "") -> str:
        """Return the captured deliverable.

        Prefers the answer frozen at the boundary; ``end_tool_answer`` (the
        legacy ``latest_answer.md``) is used **only** when nothing at all was
        registered, so a run whose whole output lived on the control turn still
        posts something (the last-resort merge, G5).
        """
        if self._snapshot is not None and self._snapshot.text:
            return self._snapshot.text
        if self._current:
            return self._current
        return end_tool_answer


def _final_message_text(payload: EngineToolPayload | None) -> str:
    """The ``message`` field of a final-message payload, or ``""``.

    Routing payloads (``dispatch_next``) carry no deliverable, so only a
    ``final_message`` payload yields text.
    """
    if payload is None or payload.payload_kind != "final_message":
        return ""
    return (payload.fields.get("message") or "").strip()


def _action_thought_text(event: object) -> str:
    """The turn text of a control-plane tool call.

    An assistant response that carries both text and a tool call surfaces the
    text here (``ActionEvent.thought``) rather than as a ``MessageEvent``, so
    the end-tool turn is the only place a shared-turn deliverable can be read.
    """
    return _content_text(getattr(event, "thought", None)).strip()


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
