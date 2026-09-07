"""
Output validation utilities for agent outputs.

Validates AGENT_RUN_CONFIG sentinels and checks for forbidden sections
to improve instruction-following compliance.

Agents emit a single-line HTML-comment sentinel carrying the dispatch JSON:

    <!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "medium"} -->

Single line + HTML comment = no multi-line boundary ambiguity and no leak
into rendered GitHub comments. This module owns the one canonical pattern;
all consumers (build_output, prepare_comment) import from here.
"""

import json
import re
import sys
from dataclasses import dataclass
from typing import Optional

# Canonical sentinel: single-line HTML comment, no DOTALL.
# [ \t] (not \s) keeps the match strictly within one line: a sentinel split
# across lines never matches. Payload is a compact JSON object; the non-greedy
# match with the mandatory trailing "-->" handles nested braces via backtracking.
AGENT_RUN_CONFIG_PATTERN = re.compile(r"<!--(?:[ \t])*AGENT_RUN_CONFIG[ \t]+(\{.*?\})(?:[ \t])*-->")

# Start-marker locator for unterminated-sentinel (truncation) detection.
# Must tolerate the same whitespace the canonical pattern allows after "<!--".
_AGENT_RUN_CONFIG_START_RE = re.compile(r"<!--\s*AGENT_RUN_CONFIG")

# Forbidden section headers that ctx agent must never include
CTX_FORBIDDEN_SECTIONS = [
    "ROOT CAUSE",
    "ANALYSIS",
    "FIX",
    "SOLUTION",
    "RECOMMENDATION",
    "PROPOSED CHANGES",
    "TWO POSSIBLE FIXES",
]


@dataclass
class ValidationResult:
    """Result of validating an agent output."""

    is_valid: bool
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def add_error(self, message: str):
        self.errors.append(message)
        self.is_valid = False


def extract_agent_run_configs(text: str) -> list[str]:
    """Extract all AGENT_RUN_CONFIG sentinel JSON payloads from text.

    Only the exact single-line sentinel form matches; a quoted example that
    spans multiple lines (as in docs/prompts) is deliberately ignored.

    Args:
        text: Agent output text.

    Returns:
        List of raw JSON payload strings, in order of appearance.
    """
    matches = AGENT_RUN_CONFIG_PATTERN.findall(text)
    return [match.strip() for match in matches if match.strip()]


# Markdown fence lines (``` / ```) sometimes wrap a sentinel. They are
# syntactic noise for JSON parsing and are dropped before the payload is read.
_FENCE_RE = re.compile(r"^[\t ]*`{3,}.*$")


@dataclass
class ExtractResult:
    """Outcome of resilient AGENT_RUN_CONFIG extraction.

    ``payload`` is the raw JSON payload of the last valid block (canonical or
    lenient path), or None. ``parse_error`` carries a diagnostic when a
    sentinel block was located but could not be parsed as a JSON object, so
    callers can distinguish "agent ended without any sentinel" from
    "sentinel present but unparseable" (issue #512).
    """

    payload: Optional[str]
    parse_error: Optional[str] = None


def _block_payload(raw_block: str) -> str:
    """Extract the top-level balanced ``{...}``/``[...]`` span of a block.

    Balances the outermost object or array while respecting JSON strings, so
    braces inside string values cannot end the span early. Returns the empty
    string when no balanced object/array is present (e.g. a truncated final
    line).
    """
    start = raw_block.find("{")
    array_start = raw_block.find("[")
    if array_start >= 0 and (start < 0 or array_start < start):
        start = array_start
    if start < 0:
        return ""
    open_ch, close_ch = raw_block[start], "}"
    if open_ch == "[":
        close_ch = "]"
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(raw_block)):
        ch = raw_block[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return raw_block[start : i + 1]
    return ""


def _clean_raw_block(raw_block: str) -> str:
    """Normalize a raw sentinel block before JSON parsing.

    Drops markdown fence lines and folds line breaks (the #511 failure shape:
    the model split the single-line sentinel and wrapped it in stray fences).
    """
    kept = [line for line in raw_block.split("\n") if not _FENCE_RE.match(line)]
    return "\n".join(kept)


def _lenient_parse(payload: str) -> tuple[Optional[str], Optional[str]]:
    """Parse one raw object payload leniently.

    Tries the payload as-is first; on failure retries with markdown fence
    lines removed (the #511 failure shape: the model wrapped the sentinel in
    stray ``` fences). Returns (payload, None) on success, (None, reason)
    when no parse succeeds.
    """
    def _loads(candidate: str):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return None
        return candidate if isinstance(data, dict) else None

    result = _loads(payload)
    if result is not None:
        return result, None

    cleaned = _clean_raw_block(payload)
    if cleaned != payload:
        result = _loads(cleaned)
        if result is not None:
            return result, None
        payload = cleaned

    try:
        json.loads(payload)
        reason = "JSON is not an object"
    except json.JSONDecodeError as e:
        reason = f"unparseable JSON ({e.msg} at line {e.lineno} column {e.colno})"
    return None, reason


def _extract_lenient_blocks(text: str) -> list[str]:
    """Locate sentinel blocks the canonical single-line pattern misses.

    For every start marker, balances braces from its first ``{`` to the
    matching close (respecting JSON strings) so a line-broken, optionally
    fence-wrapped payload still resolves to its exact object span. Each block
    is bounded by the next start marker so a later sentinel cannot bleed into
    it. Returns the raw object spans in order of appearance.
    """
    markers = list(_AGENT_RUN_CONFIG_START_RE.finditer(text))
    results: list[str] = []
    for i, m in enumerate(markers):
        bound = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        payload = _block_payload(text[m.start() :bound])
        if payload:
            results.append(payload)
    return results


def extract_dispatch_resilient(text: str) -> ExtractResult:
    """Extract the last valid AGENT_RUN_CONFIG payload, resilient to drift.

    Every start marker is located in document order (single-line or not) and
    its balanced ``{...}`` span parsed leniently, so a line-broken or
    fence-wrapped payload (issue #511) still resolves. "Last valid one wins"
    is preserved uniformly across both shapes; invalid payloads are skipped.

    Args:
        text: Agent output text.

    Returns:
        ExtractResult with the payload and a parse diagnostic when a
        sentinel marker was present but no payload could be parsed.
    """
    if not text or not _AGENT_RUN_CONFIG_START_RE.search(text):
        return ExtractResult(payload=None)

    candidates = _extract_lenient_blocks(text)
    if not candidates:
        # Marker present but no balanced object: truncated final line.
        return ExtractResult(payload=None, parse_error="truncated JSON object")

    last_valid: Optional[str] = None
    last_error: Optional[str] = None
    for payload in candidates:
        parsed, error = _lenient_parse(payload)
        if parsed is not None:
            last_valid = parsed
            last_error = None
        elif error is not None:
            last_error = error
    return ExtractResult(payload=last_valid, parse_error=last_error)


def extract_dispatch(text: str) -> Optional[str]:
    """Return the raw JSON payload of the last valid-JSON sentinel.

    Invalid JSON payloads are skipped (earlier valid blocks still win).

    Args:
        text: Agent output text.

    Returns:
        The last valid JSON payload string, or None if no valid block exists.
    """
    for payload in reversed(extract_agent_run_configs(text)):
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return payload
    return None


def has_unterminated_agent_run_config(text: str) -> bool:
    """Detect a truncated sentinel (start marker present, no closing "-->").

    A complete sentinel always contains its own "-->" after the start
    marker, so the first occurrence whose tail lacks "-->" marks a
    truncation of the final line of the message.
    """
    for match in _AGENT_RUN_CONFIG_START_RE.finditer(text):
        if "-->" not in text[match.start():]:
            return True
    return False


def _remove_lenient_sentinels(text: str) -> str:
    """Drop line-broken / fence-wrapped sentinel spans (lenient path).

    For each remaining start marker, cut from the marker to the end of its
    balanced object plus the closing ``-->`` when one follows shortly after.
    """
    result = text
    for m in reversed(list(_AGENT_RUN_CONFIG_START_RE.finditer(result))):
        span = result[m.start() :]
        payload = _block_payload(span)
        if not payload:
            continue
        start = m.start() + span.find(payload[0])
        end = start + len(payload)
        tail = result[end :].lstrip()
        if tail.startswith("-->"):
            end += len(result[end :]) - len(tail) + 3
        result = result[:m.start()] + result[end:]
    return result


def has_agent_run_config_marker(text: str) -> bool:
    """True when an AGENT_RUN_CONFIG start marker is present in text.

    Public accessor for the start-marker locator, used by the resolver to
    tell apart "agent ended without any sentinel" from "sentinel present
    but no usable dispatch stored" (issue #512).
    """
    return _AGENT_RUN_CONFIG_START_RE.search(text) is not None


def strip_agent_run_config(text: str) -> str:
    """Remove all AGENT_RUN_CONFIG sentinels from text.

    A sentinel occupying a whole line drops that line; an inline sentinel is
    removed in place. Line-broken or fence-wrapped sentinels (issue #511) are
    removed via the lenient locator so a recovered payload does not leak into
    the visible comment. A trailing unterminated sentinel (truncated last
    line) is cut off so no partial marker leaks into rendered comments.

    Args:
        text: Agent output text.

    Returns:
        Text without any AGENT_RUN_CONFIG sentinel, stripped of outer whitespace.
    """
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("<!--") and AGENT_RUN_CONFIG_PATTERN.fullmatch(stripped):
            continue  # whole-line sentinel: drop the line
        lines.append(AGENT_RUN_CONFIG_PATTERN.sub("", line))
    cleaned = "\n".join(lines)

    cleaned = _remove_lenient_sentinels(cleaned)

    match = _AGENT_RUN_CONFIG_START_RE.search(cleaned)
    if match and has_unterminated_agent_run_config(cleaned):
        cleaned = cleaned[: match.start()]
    # Collapse blank lines left behind by stripped whole-line sentinels.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def validate_agent_run_config(json_str: str) -> ValidationResult:
    """Validate a single AGENT_RUN_CONFIG JSON payload."""
    result = ValidationResult(is_valid=True)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        result.add_error(f"Invalid JSON in AGENT_RUN_CONFIG: {e}")
        return result

    if not isinstance(data, dict):
        result.add_error("AGENT_RUN_CONFIG must be a JSON object")
        return result

    # task is required
    if "task" not in data:
        result.add_error("AGENT_RUN_CONFIG missing required field: 'task'")

    # Validate known fields' types
    valid_tasks = {"genius", "ctx", "dev", "review", "issue", "issue-reviewer"}
    if data.get("task") and data["task"] not in valid_tasks:
        result.add_error(
            f"Invalid task '{data['task']}'. Must be one of: {valid_tasks}"
        )

    valid_complexities = {"low", "medium", "high"}
    if data.get("complexity") and data["complexity"] not in valid_complexities:
        result.add_error(
            f"Invalid complexity '{data['complexity']}'. "
            f"Must be one of: {valid_complexities}"
        )

    valid_reasoning = {"none", "low", "medium", "high"}
    if data.get("reasoning") and data["reasoning"] not in valid_reasoning:
        result.add_error(
            f"Invalid reasoning '{data['reasoning']}'. "
            f"Must be one of: {valid_reasoning}"
        )

    # Validate numeric fields
    for field in ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty"):
        if data.get(field) is not None:
            val = data[field]
            if not isinstance(val, (int, float)):
                result.add_error(f"'{field}' must be a number, got {type(val).__name__}")
            elif field == "top_p" and not (0 <= val <= 1):
                result.add_error(f"'{field}' must be in range [0, 1], got {val}")
            elif field == "min_p" and not (0 <= val <= 1):
                result.add_error(f"'{field}' must be in range [0, 1], got {val}")
            elif field == "presence_penalty" and not (-2.0 <= val <= 2.0):
                result.add_error(f"'{field}' must be in range [-2.0, 2.0], got {val}")
            elif field in ("top_k", "repetition_penalty") and val <= 0:
                result.add_error(f"'{field}' must be positive, got {val}")

    if data.get("round") is not None and not isinstance(data["round"], int):
        result.add_error("'round' must be an integer")

    return result


VALID_DISPATCH_STATUSES = {"advance", "loop", "hold", "terminate"}


def validate_dispatch(data: dict) -> ValidationResult:
    """Validate a dispatch dict (from WorkflowState.dispatch).

    Args:
        data: Dict containing dispatch fields.

    Returns:
        ValidationResult with any issues found.
    """
    result = ValidationResult(is_valid=True)

    if not isinstance(data, dict):
        result.add_error("dispatch must be a JSON object")
        return result

    status = data.get("status")
    if status and status not in VALID_DISPATCH_STATUSES:
        result.add_error(
            f"Invalid dispatch status '{status}'. Must be one of: {VALID_DISPATCH_STATUSES}"
        )

    # Validate task_hint if present
    valid_tasks = {"genius", "ctx", "dev", "review", "issue", "issue-reviewer"}
    if data.get("task_hint") and data["task_hint"] not in valid_tasks:
        result.add_error(
            f"Invalid task_hint '{data['task_hint']}'. Must be one of: {valid_tasks}"
        )

    # Reuse AGENT_RUN_CONFIG validators for shared fields
    config_result = validate_agent_run_config(json.dumps(data))
    # Filter out 'task' errors since dispatch uses 'task_hint' instead
    filtered = [e for e in config_result.errors if "missing required field: 'task'" not in e]
    for error in filtered:
        result.add_error(error)

    return result


def validate_forbidden_sections(
    text: str, forbidden_headers: Optional[list[str]] = None
) -> ValidationResult:
    """Check if text contains forbidden section headers.

    Matches section headers (e.g. ``## ROOT CAUSE``, ``### FIX``) rather
    than arbitrary substrings, to avoid false positives like "RESOLUTION"
    matching "SOLUTION".

    Args:
        text: The agent output text to check.
        forbidden_headers: List of section headers to forbid. Defaults to CTX_FORBIDDEN_SECTIONS.

    Returns:
        ValidationResult with is_valid=False if any forbidden headers are found.
    """
    result = ValidationResult(is_valid=True)
    if forbidden_headers is None:
        forbidden_headers = CTX_FORBIDDEN_SECTIONS

    for header in forbidden_headers:
        # Build a case-insensitive pattern that matches the header as a
        # standalone section heading (preceded by ##/### or start of line).
        pattern = re.compile(
            r"(^|\n)\s*#{1,6}\s*" + re.escape(header) + r"\b",
            re.IGNORECASE,
        )
        if pattern.search(text):
            result.add_error(f"Found forbidden section header: '{header}'")

    return result


def validate_agent_output(
    text: str,
    role: str = "",
) -> ValidationResult:
    """Validate full agent output based on role-specific rules.

    Args:
        text: The agent output text.
        role: The agent role name (e.g., "ctx", "dev").

    Returns:
        ValidationResult containing all found issues.
    """
    result = ValidationResult(is_valid=True)

    # Validate all AGENT_RUN_CONFIG sentinel payloads
    configs = extract_agent_run_configs(text)
    if configs:
        for i, config_str in enumerate(configs):
            config_result = validate_agent_run_config(config_str)
            for error in config_result.errors:
                result.add_error(f"AGENT_RUN_CONFIG[{i}]: {error}")

    # Role-specific validation
    if role == "ctx":
        forbidden_result = validate_forbidden_sections(text)
        for error in forbidden_result.errors:
            result.add_error(error)

    return result


def main():
    """CLI entry point: validate output from stdin or file."""
    if len(sys.argv) < 2:
        print("Usage: python validate_output.py <file> [role]")
        print("  file: path to agent output file")
        print("  role: agent role (ctx, dev, etc.) for role-specific checks")
        sys.exit(1)

    filepath = sys.argv[1]
    role = sys.argv[2] if len(sys.argv) > 2 else ""

    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    result = validate_agent_output(text, role)

    if result.is_valid:
        print("VALID: No issues found")
        sys.exit(0)
    else:
        print(f"INVALID: {len(result.errors)} issue(s) found:")
        for error in result.errors:
            print(f"  - {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
