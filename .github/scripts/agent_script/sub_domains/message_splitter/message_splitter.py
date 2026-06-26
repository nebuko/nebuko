"""Message splitting — pure functions for splitting long messages into parts."""

import re


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
