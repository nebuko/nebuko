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

import json
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


def _report_argument(event: object) -> str:
    """The ``report`` argument of a ``finalize`` call, or ``""``.

    A call that passes validation carries it on the typed action. A call the SDK
    rejected (a missing or invalid ``turn_outcome``) is recorded with
    ``action=None`` and only the raw tool-call arguments, which still hold the
    report the agent wrote - so it is read from there rather than lost.
    """
    report = getattr(getattr(event, "action", None), "report", None)
    if isinstance(report, str):
        return report.strip()
    raw = getattr(getattr(event, "tool_call", None), "arguments", None)
    if not isinstance(raw, str):
        return ""
    try:
        arguments = json.loads(raw)
    except ValueError:
        return ""
    report = arguments.get("report") if isinstance(arguments, dict) else None
    return report.strip() if isinstance(report, str) else ""


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
        if is_finalize_call(events[index]):
            return index
    return -1


def is_finalize_call(event: object) -> bool:
    """Whether ``event`` is a ``finalize`` call, not the observation answering it.

    The SDK emits the call's observation right after it, and the observation
    carries the same ``tool_name``. Matching on the name alone lands on the
    observation, which has no report, so the report the call carried is never
    read (issue #1038). Only a call carries the raw ``tool_call``.
    """
    return (
        getattr(event, "tool_name", None) == "finalize"
        and getattr(event, "tool_call", None) is not None
    )


def closing_turn_report_text(conversation: Any) -> str:
    """Report text anchored to the harvested ``finalize`` event's turn.

    Precedence: the anchor ``finalize`` call's ``report`` argument (the canonical
    channel, issue #1038), including from a call the SDK rejected; else the action event's ``thought`` (a
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
    argument = _report_argument(events[anchor])
    if argument:
        return argument
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
        if is_finalize_call(events[index]) or (
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
