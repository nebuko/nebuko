"""Message splitting — pure functions for splitting long messages into parts."""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from nebuko.engine.lib.config import CommentSizePolicy

# ``prepare_comment`` sits at the ``lib`` root; this module is a sub-domain three
# levels down, so importing it needs an explicit bootstrap rather than the
# caller's ``sys.path``. ``parents[4]`` is the ``engine`` directory: putting it
# on ``sys.path`` makes ``lib`` reachable as the ``nebuko.engine.lib`` package,
# which the rest of the run already imports from.
from nebuko.engine.lib.prepare_comment import ANSWER_CONTINUATION_TOKEN


def split_message(message: str, max_length: int = 10000) -> list[str]:
    """
    Split a long message into parts, each under max_length.

    Splits preferentially at paragraph breaks, then line breaks,
    then sentence boundaries, falling back to hard splits.

    Args:
        message: The full message to split.
        max_length: Maximum characters per part.

    Returns:
        List of raw message parts, each <= max_length characters.
    """
    if not message or len(message) <= max_length:
        return [message] if message else []

    parts: list[str] = []
    remaining = message

    while remaining:
        if len(remaining) <= max_length:
            parts.append(remaining)
            break

        # Strategy 1: Split at paragraph boundaries (\n\n)
        part = _split_at_boundaries(remaining, max_length, "\n\n")
        if part is not None:
            parts.append(part)
            remaining = remaining[len(part):].lstrip("\n")
            continue

        # Strategy 2: Split at line boundaries (\n)
        part = _split_at_boundaries(remaining, max_length, "\n")
        if part is not None:
            parts.append(part)
            remaining = remaining[len(part):].lstrip("\n")
            continue

        # Strategy 3: Split at sentence boundaries (. ! ?)
        part = _split_at_sentence_boundaries(remaining, max_length)
        if part is not None:
            parts.append(part)
            remaining = remaining[len(part):].lstrip(" ")
            continue

        # Strategy 4: Hard split at max_length
        parts.append(remaining[:max_length])
        remaining = remaining[max_length:]

    return parts


def _split_at_boundaries(text: str, max_length: int, delimiter: str) -> str | None:
    """
    Try to split text at delimiter boundaries, keeping each part <= max_length.

    Uses greedy packing: accumulate segments until adding the next would
    exceed max_length, then emit the current part.

    Returns the first part (without the delimiter), or None if text is
    already <= max_length or splitting is not possible.
    """
    if len(text) <= max_length:
        return None

    segments = text.split(delimiter)
    current_parts: list[str] = []
    current_len = 0

    for segment in segments:
        # Calculate length if we add this segment
        separator_len = len(delimiter) if current_parts else 0
        new_len = current_len + separator_len + len(segment)

        if new_len > max_length and current_parts:
            # Current part is full, emit it
            part = delimiter.join(current_parts)
            return part
        elif new_len > max_length and not current_parts:
            # Even the first segment is too long — can't use this strategy
            return None
        else:
            current_parts.append(segment)
            current_len = new_len

    # All segments fit in one part
    return None


def _split_at_sentence_boundaries(text: str, max_length: int) -> str | None:
    """
    Try to split text at sentence boundaries (. ! ?) followed by space.

    Returns the first part (including the sentence-ending punctuation),
    or None if splitting is not possible.
    """
    if len(text) <= max_length:
        return None

    # Find the last sentence boundary before max_length
    cutoff = text[:max_length]
    # Look for ". ", "! ", or "? " patterns
    last_break = -1
    for marker in [". ", "! ", "? "]:
        pos = cutoff.rfind(marker)
        if pos > last_break:
            last_break = pos

    if last_break > max_length * 0.3:  # Only use if we're past 30% of the limit
        return cutoff[:last_break + 1]  # Include the punctuation

    return None


# ── Multi-comment splitting (issue #913) ─────────────────────────
#
# A long answer is posted across several comments instead of being truncated:
# one comment per ``CommentPart``, each within ``CommentSizePolicy``'s per
# comment budget. The state-bearing header rides on the *first* part (see
# ``split_comment_body``), so the continuation parts that follow it group onto
# the same history entry (issue #504), and every part carries the bot marker,
# which keeps the whole run CI-gate exempt instead of re-firing the gate.

#: Appended to the final part when a body overflows the ``max_parts`` ceiling.
_TRUNCATION_MARKER = "\n\n...*(comment truncated due to size limit)*"


@dataclass(frozen=True)
class CommentPart:
    """One comment's worth of an answer, framed with its continuation header."""

    index: int
    total: int
    body: str


def split_comment_body(
    body: str,
    policy: CommentSizePolicy,
    head: str = "",
    marker: str = "",
) -> list[CommentPart]:
    """Split an answer body across comments, prepending ``head`` to the first part.

    Boundary-aware (paragraph, then line, then sentence, then hard split) and
    policy-driven: each part stays within ``policy``'s per-comment budget once
    its ``*(Part i/N)*`` header is counted. ``head`` (the answer header, which
    carries the workflow-state section and the bot marker) is prepended to the
    *first* comment, not the last: the state marker must land on the earliest
    part so the continuation parts that follow it are grouped onto the same
    history entry (issue #504), and the leading bot marker keeps the whole run
    exempt from the CI gate. If the body needs more than ``policy``'s
    ``max_parts`` comments, the overflow is folded into the last part and
    marked with a truncation notice rather than dropped silently.

    Args:
        body: The answer content to post.
        policy: The comment-size policy (per-comment budget and part ceiling).
        head: Content prepended verbatim to the first part (e.g. the answer
            header with its state section); only its length affects sizing.
        marker: A bot marker line (e.g. ``<!-- bot-agent-<role> -->\n``)
            prefixed to every *continuation* part. Without it the later parts
            are marker-less and the thread ledger classifies them as human
            feedback, so they never render into the next agent's prompt — the
            very content this split exists to deliver (issue #913). The first
            part already carries the marker inside ``head``, so it is not
            prefixed there.

    Returns:
        The ordered parts; a single part carries no continuation header.
    """
    budget = policy.resolved_max_comment_chars()
    max_parts = policy.resolved_max_parts()

    # Single-comment fast path: no continuation header is emitted for one part.
    if len(body) + len(head) <= budget:
        return [CommentPart(index=1, total=1, body=head + body)]

    header_reserve = len(f"*(Part {max_parts}/{max_parts})*\n\n") + len(marker)
    per_part = max(1, budget - header_reserve)
    first_limit = max(1, per_part - len(head))
    # Only the continuation parts carry the inertness stamp (issue #1002), so
    # their content budget is the smaller one; part 1 keeps the full budget and
    # its boundary-aware packing.
    continuation_limit = max(1, per_part - len(_continuation_header()))

    chunks = _pack_body(body, first_limit, continuation_limit)
    if len(chunks) > max_parts:
        chunks = _fold_overflow(chunks, max_parts, continuation_limit)

    total = len(chunks)
    parts: list[CommentPart] = []
    for index, chunk in enumerate(chunks, start=1):
        part_header = f"*(Part {index}/{total})*\n\n"
        if index == 1:
            text = head + part_header + chunk
        else:
            # Continuation parts are inert (issue #1002): the stamp is the
            # gate's negative match, and the payload they would otherwise
            # re-post is neutralized the same way ``escape_agent_triggers``
            # neutralizes a stray ``@@`` token.
            text = marker + _continuation_header() + part_header + _neutralize_triggers(chunk)
        parts.append(CommentPart(index=index, total=total, body=text))
    return parts


def _continuation_header() -> str:
    """The inertness stamp prepended to every continuation part (issue #1002)."""
    return f"{ANSWER_CONTINUATION_TOKEN}\n"


def _neutralize_triggers(content: str) -> str:
    """Break the trigger payloads a continuation part must not re-post (#1002).

    The issue_comment gate fires on the ``<!-- workflow-continue -->`` and
    ``<!-- bot-trigger:`` substrings and on raw ``@@agent`` tokens. Escaping the
    markers (rather than deleting them) keeps the quoted text readable while the
    substring match fails; the trigger payload itself rides on part 1 only.
    """
    content = re.sub(
        r"<!--\s*(workflow-continue|bot-trigger:)\s*(-->)?",
        lambda m: m.group(0).replace("<!--", "<!----", 1),
        content,
    )
    return re.sub(
        r"(?<![\w])@@([A-Za-z0-9_-]+)",
        lambda m: m.group(0).replace("@@", "@@\u200b", 1),
        content,
    )


def _pack_body(body: str, first_limit: int, continuation_limit: int) -> list[str]:
    """Greedily split ``body`` into a first chunk within ``first_limit`` and continuations within ``continuation_limit``.

    The two limits differ because the parts are framed differently: part 1
    carries the answer header (which may be long) instead of the continuation
    stamp, so charging it the stamp's room would needlessly shrink the first
    chunk below a paragraph boundary (issue #1002).
    """
    chunks: list[str] = []
    remaining = body
    limit = first_limit
    while len(remaining) > limit:
        chunk, remaining = _take_chunk(remaining, limit)
        if not chunk:  # defensive: guarantee progress so the loop always ends
            chunk, remaining = remaining[:limit], remaining[limit:]
        chunks.append(chunk)
        limit = continuation_limit
    chunks.append(remaining)
    return chunks


def _fold_overflow(chunks: list[str], max_parts: int, last_limit: int) -> list[str]:
    """Keep the first ``max_parts`` chunks, folding the rest into the last with a marker."""
    kept = chunks[:max_parts]
    overflow = "".join(chunks[max_parts - 1:])
    room = max(1, last_limit - len(_TRUNCATION_MARKER))
    truncated, _ = _take_chunk(overflow, room)
    kept[-1] = truncated + _TRUNCATION_MARKER
    return kept


def _take_chunk(text: str, limit: int) -> tuple[str, str]:
    """Return the largest boundary-aligned prefix of ``text`` within ``limit`` and the rest."""
    if limit <= 0 or not text:
        return "", text
    if len(text) <= limit:
        return text, ""

    part = _split_at_boundaries(text, limit, "\n\n")
    if part is None:
        part = _split_at_boundaries(text, limit, "\n")
    if part is None:
        part = _split_at_sentence_boundaries(text, limit)
    if not part:
        part = text[:limit]
        return part, text[limit:]
    return part, text[len(part):].lstrip("\n")
