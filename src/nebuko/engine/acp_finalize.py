"""Closing turn for the Claude Code (ACP) backend.

The SDK agent ends a step by calling the ``finalize`` tool, which carries the
report and the typed ``turn_outcome``. Claude Code has no such tool, so the
engine asks for the same two artifacts as plain text in one closing turn: the
markdown report, then one fenced ``nebuko-turn-outcome`` JSON block holding the
``turn_outcome``. This module owns that wire format: the prompt, and the parser
that splits a reply back into ``(report, TurnOutcomePayload)``.

The payload is the same :class:`~nebuko.engine.finalize.TurnOutcomePayload` the
``finalize`` tool validates, so the engine routes it exactly like a tool call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from nebuko.engine.finalize import TurnOutcomePayload

__all__ = [
    "ACP_CLOSING_PROMPT",
    "ACP_CORRECTION_PROMPT",
    "ACP_FENCE_TAG",
    "ClosingReply",
    "parse_closing_reply",
]

ACP_FENCE_TAG = "nebuko-turn-outcome"

#: ``{sections}`` is the step's declared section skeleton (empty without one).
ACP_CLOSING_PROMPT = (
    "Engine closing turn - the work phase is over; do not use any tool. Reply "
    "with plain text only, in two parts.\n\n"
    "1. Your COMPLETE, self-contained markdown report: the full deliverable "
    "that is posted on the issue (never a one-line note). Nothing is posted if "
    "it is empty.\n"
    "2. As the very last thing in your reply, exactly one fenced block tagged "
    f"`{ACP_FENCE_TAG}` holding your turn outcome as JSON:\n\n"
    f"```{ACP_FENCE_TAG}\n"
    '{{"appreciation": "approved", "note": "one sentence"}}\n'
    "```\n\n"
    "{sections}\n"
    "`appreciation` is required: 'approved' when the work meets this step's "
    "goal, 'rejected' when it does not, 'hold' only when you are blocked and "
    "need information from a human (then also set `hold_kind` to "
    "'awaiting_input'). Optional keys: `agent_call` = {{\"agent_name\", "
    "\"task\"}} when you are imperatively naming the exact agent to run next, "
    "`round`, `note`, `complexity` ('low' | 'medium' | 'high') and `pin` "
    "(true | false). Where the workflow goes next is engine topology, never "
    "your choice."
)

ACP_CORRECTION_PROMPT = (
    "Your previous reply had no usable turn outcome: {problem}. Reply again "
    "with the complete report followed by exactly one valid "
    f"`{ACP_FENCE_TAG}` fenced JSON block as the last thing in the reply."
)

_FENCE_RE = re.compile(
    r"```[ \t]*(?P<tag>[A-Za-z0-9_-]*)[ \t]*\n(?P<body>.*?)\n?```",
    re.DOTALL,
)


@dataclass(frozen=True)
class ClosingReply:
    """A closing-turn reply split into its report and turn outcome.

    ``outcome`` is ``None`` when no valid block was found; ``problem`` then
    names why, so a retry prompt can tell the model what to fix.
    """

    report: str
    outcome: Optional[TurnOutcomePayload]
    problem: str = ""


def parse_closing_reply(text: str) -> ClosingReply:
    """Split ``text`` into the report and the validated ``turn_outcome``.

    The last block tagged :data:`ACP_FENCE_TAG` wins (an untagged ``json`` block
    carrying an ``appreciation`` key is accepted as a fallback). The block is
    removed from the report either way, so the posted comment never carries it.
    """
    matches = list(_FENCE_RE.finditer(text or ""))
    chosen = _pick_block(matches)
    if chosen is None:
        return ClosingReply(
            report=(text or "").strip(),
            outcome=None,
            problem=f"no `{ACP_FENCE_TAG}` block found",
        )
    report = (text[: chosen.start()] + text[chosen.end() :]).strip()
    try:
        data = json.loads(chosen.group("body"))
        outcome = TurnOutcomePayload.model_validate(data)
        outcome.validate_runtime()
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        return ClosingReply(report=report, outcome=None, problem=_one_line(exc))
    return ClosingReply(report=report, outcome=outcome)


def _pick_block(matches: list[re.Match[str]]) -> Optional[re.Match[str]]:
    tagged = [m for m in matches if m.group("tag") == ACP_FENCE_TAG]
    if tagged:
        return tagged[-1]
    for match in reversed(matches):
        if match.group("tag") == "json" and '"appreciation"' in match.group("body"):
            return match
    return None


def _one_line(error: Exception) -> str:
    return " ".join(str(error).split())
