"""Comment classification for the thread ledger (issue #872).

Maps one raw GitHub issue artifact (a comment body, or the issue body handled by
the reconciler) to a :class:`ThreadRecordKind`. Pure: the caller resolves the
author's bot-ness and this function only reads the body's own markers, reusing
the engine's established marker conventions (``<!-- bot-agent-* -->``,
``<!-- bot-human-action -->``, the agent-start notice line, the crash-path
fallback lines, the ``/nebuko`` / ``@@agent`` command grammar).

Priority matters: an *engine* notice is detected before the generic
``bot-agent-*`` marker because the agent-start acknowledgment also carries a
``bot-agent-*`` marker but is engine bookkeeping, not an agent's answer.
"""

from __future__ import annotations

import re

from ...sub_domains.thread_record.thread_record import ThreadRecordKind

__all__ = ["classify_comment"]

# Engine-authored notice anchors (HumanActionNotice marker; AgentStartNotice
# line; prepare_comment crash-path fallbacks).
_HUMAN_ACTION_MARKER = "<!-- bot-human-action -->"
_AGENT_STARTED_FRAGMENT = "agent started**"
_RUN_LOG_FRAGMENT = "View workflow run logs"
_RUNNER_ERROR_FRAGMENTS = (
    "The agent crashed or timed out before producing output",
    "The agent did not produce a final message",
)

_AGENT_MARKER_RE = re.compile(r"<!-- bot-agent-[a-z0-9\-]+ -->")
_NEBUKO_COMMAND_RE = re.compile(r"^\s*/nebuko\b", re.MULTILINE)
_AGENT_MENTION_RE = re.compile(r"@@[a-z][a-z0-9\-]*")


def classify_comment(body: str, *, author_is_bot: bool) -> ThreadRecordKind:  # @stub-protected
    """Classify one comment body into a :class:`ThreadRecordKind`.

    Args:
        body: The raw comment body (markers included).
        author_is_bot: Whether the comment's author is a bot account. Resolved
            by the caller so the classifier stays a pure function of text.

    Returns:
        The artifact's kind. Engine notices win over the generic agent marker;
        a bot comment with no marker is still engine output; a non-bot comment
        is a command only when it carries the ``/nebuko`` / ``@@agent`` grammar.
    """
    text = body or ""
    if _is_engine_notice(text):
        return ThreadRecordKind.ENGINE_OUTPUT
    if _AGENT_MARKER_RE.search(text):
        return ThreadRecordKind.AGENT_OUTPUT
    if author_is_bot:
        return ThreadRecordKind.ENGINE_OUTPUT
    if _is_command(text):
        return ThreadRecordKind.COMMAND
    return ThreadRecordKind.HUMAN_FEEDBACK


def _is_engine_notice(text: str) -> bool:
    """True for the engine's own notices, however they are framed."""
    if _HUMAN_ACTION_MARKER in text:
        return True
    if _AGENT_STARTED_FRAGMENT in text and _RUN_LOG_FRAGMENT in text:
        return True
    return any(fragment in text for fragment in _RUNNER_ERROR_FRAGMENTS)


def _is_command(text: str) -> bool:
    """True for a human command: a ``/nebuko`` slash line or an ``@@agent`` ping."""
    return bool(_NEBUKO_COMMAND_RE.search(text) or _AGENT_MENTION_RE.search(text))
