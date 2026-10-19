"""The closing turn's report text, harvested from the conversation events (#1038).

The engine-elicited closing turn ends with one ``finalize`` call whose ``report``
argument is the report (its turn text is the fallback). Both the host (which posts the report) and the
``finalize`` executor (which refuses a text-less call) read the report through
this one module, so they agree on what "the report" is.

Events after the anchor ``finalize`` call (an acknowledgement such as "Report
posted above") can never be the report: the anchor is the last ``finalize``
action at or after :data:`REPORT_OFFSET_ATTR`, and the trailing messages are
ignored.
"""

from __future__ import annotations

from typing import Any

#: Conversation attribute holding the event index where the closing turn starts
#: (set by the host before it runs the turn).
REPORT_OFFSET_ATTR = "nebuko_report_event_offset"


def events_of(conversation: Any) -> list:
    """The conversation's event list, or ``[]`` when it exposes none."""
    return list(getattr(getattr(conversation, "state", None), "events", None) or [])


def content_text(content: object) -> str:
    """Join an SDK message content value into plain text (mirrors the capture)."""
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for item in content:  # type: ignore[union-attr]
        text = getattr(item, "text", None)
        parts.append(text if isinstance(text, str) else str(item))
    return "".join(parts)


def finalize_anchor_index(conversation: Any) -> int:
    """Index of the harvested ``finalize`` event, or ``-1`` when there is none.

    Both the report text and the route read from this single anchor: the last
    ``finalize`` action event at or after :data:`REPORT_OFFSET_ATTR`. Events after
    the ``finalize`` call (a trailing status message, a repeated call) can never
    move the anchor, so the posted body and the recorded route always come from
    the same turn (issue #1014).
    """
    offset = getattr(conversation, REPORT_OFFSET_ATTR, 0) or 0
    events = events_of(conversation)
    for index in range(len(events) - 1, offset - 1, -1):
        if getattr(events[index], "tool_name", None) == "finalize":
            return index
    return -1


def closing_turn_report_text(conversation: Any) -> str:
    """Report text anchored to the harvested ``finalize`` event's turn.

    Precedence: the anchor ``finalize`` action's typed ``report`` argument (the
    canonical channel, issue #1038); else the action event's ``thought`` (a
    model that wrote the report as turn text; the SDK emits no standalone
    ``MessageEvent`` for a turn that also carries a tool call); else the nearest
    preceding *agent-authored* message when the tool call and its text landed on
    different turns. Never an event after that ``finalize`` call.

    The fallback accepts ``source == "agent"`` messages only: the engine's own
    finalize prompt is a ``user``-sourced ``MessageEvent`` that embeds the
    section skeleton (issue #1020), and harvesting it posts the template.
    """
    anchor = finalize_anchor_index(conversation)
    if anchor < 0:
        return ""
    events = events_of(conversation)
    argument = getattr(getattr(events[anchor], "action", None), "report", None)
    if isinstance(argument, str) and argument.strip():
        return argument.strip()
    text = content_text(getattr(events[anchor], "thought", None)).strip()
    if text:
        return text
    # This closing turn starts at its own prompt (a user ``MessageEvent``) and
    # ends at the anchor. An earlier ``finalize`` call (a refused or recorded
    # attempt) also bounds the window: the messages after it are that attempt's
    # acknowledgements, never this turn's report (issue #1038).
    offset = getattr(conversation, REPORT_OFFSET_ATTR, 0) or 0
    start = offset
    for index in range(anchor - 1, offset - 1, -1):
        if getattr(events[index], "tool_name", None) == "finalize" or (
            getattr(events[index], "source", None) == "user"
        ):
            start = index + 1
            break
    for index in range(anchor - 1, start - 1, -1):
        if getattr(events[index], "tool_name", None) is not None:
            continue
        if getattr(events[index], "source", None) != "agent":
            continue
        message = getattr(events[index], "llm_message", None)
        if message is None:
            continue
        text = content_text(getattr(message, "content", None)).strip()
        if text:
            return text
    return ""
