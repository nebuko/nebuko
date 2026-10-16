"""
Output validation utilities for agent outputs.

Validates the harness-asserted **turn outcome** reported by the agent's
``dispatch_next`` tool call (issue #964) and checks for forbidden sections to
improve instruction-following compliance.

The legacy ``<!-- AGENT_RUN_CONFIG ... -->`` sentinel is no longer produced or
read: the agent's verdict travels on the typed tool call and the engine owns
routing. Only :func:`strip_agent_run_config` survives, as comment hygiene for
stale echoes of the deleted marker in historical prose.
"""

import json
import re
import sys
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError as PydanticValidationError


# Canonical sentinel: single-line HTML comment, no DOTALL.
# [ \t] (not \s) keeps the match strictly within one line: a sentinel split
# across lines never matches. Payload is a compact JSON object; the non-greedy
# match with the mandatory trailing "-->" handles nested braces via backtracking.
AGENT_RUN_CONFIG_PATTERN = re.compile(r"<!--(?:[ \t])*AGENT_RUN_CONFIG[ \t]+(\{.*?\})(?:[ \t])*-->")

# Start-marker locator for unterminated-sentinel (truncation) detection.
# Must tolerate the same whitespace the canonical pattern allows after "<!--".
_AGENT_RUN_CONFIG_START_RE = re.compile(r"<!--\s*AGENT_RUN_CONFIG")

# Forbidden section headers that context agent must never include
CONTEXT_FORBIDDEN_SECTIONS = [
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


def strip_agent_run_config(text: str) -> str:
    """Remove any stale ``AGENT_RUN_CONFIG`` marker from text (hygiene only).

    The sentinel is no longer produced or read (issue #964). Historical prose
    may still echo one; this keeps such an echo out of a rendered comment.
    A sentinel occupying a whole line drops that line, a trailing unterminated
    marker is cut off, and blank lines left behind are collapsed.
    """
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("<!--") and AGENT_RUN_CONFIG_PATTERN.fullmatch(stripped):
            continue  # whole-line sentinel: drop the line
        lines.append(AGENT_RUN_CONFIG_PATTERN.sub("", line))
    cleaned = "\n".join(lines)

    match = _AGENT_RUN_CONFIG_START_RE.search(cleaned)
    if match and "-->" not in cleaned[match.start():]:
        cleaned = cleaned[: match.start()]
    # Collapse blank lines left behind by stripped whole-line sentinels.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def validate_complexity_review(data) -> ValidationResult:
    """Enforce the ``complexity_review`` request contract (issue #869).

    A review request must carry an evidence bundle with at least one populated
    family and a non-blank ``feeling`` — a tier change is never free-standing.

    Args:
        data: A ``ComplexityReviewRequest``/``ComplexityReviewAction`` instance
            or a dict carrying ``evidence``/``feeling``.

    Returns:
        ValidationResult listing every contract violation found.
    """
    from complexity_governor.evidence import EvidenceBundle, is_empty_evidence

    result = ValidationResult(is_valid=True)
    if isinstance(data, dict):
        evidence = data.get("evidence")
        feeling = data.get("feeling", "")
    else:
        evidence = getattr(data, "evidence", None)
        feeling = getattr(data, "feeling", "")

    if evidence is None:
        result.add_error("complexity_review: 'evidence' is required")
    else:
        if isinstance(evidence, dict):
            try:
                evidence = EvidenceBundle.model_validate(evidence)
            except PydanticValidationError as exc:
                result.add_error(f"complexity_review: invalid evidence ({exc})")
                evidence = None
        if evidence is not None and is_empty_evidence(evidence):
            result.add_error(
                "complexity_review: evidence must populate at least one family"
            )

    if not str(feeling or "").strip():
        result.add_error(
            "complexity_review: 'feeling' is required and must not be blank"
        )

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
        forbidden_headers: List of section headers to forbid. Defaults to CONTEXT_FORBIDDEN_SECTIONS.

    Returns:
        ValidationResult with is_valid=False if any forbidden headers are found.
    """
    result = ValidationResult(is_valid=True)
    if forbidden_headers is None:
        forbidden_headers = CONTEXT_FORBIDDEN_SECTIONS

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
        role: The agent role name (e.g., "context", "dev").

    Returns:
        ValidationResult containing all found issues.
    """
    result = ValidationResult(is_valid=True)

    # Role-specific validation
    if role == "context":
        forbidden_result = validate_forbidden_sections(text)
        for error in forbidden_result.errors:
            result.add_error(error)

    return result


def main():
    """CLI entry point: validate output from stdin or file."""
    if len(sys.argv) < 2:
        print("Usage: python validate_output.py <file> [role]")
        print("  file: path to agent output file")
        print("  role: agent role (context, dev, etc.) for role-specific checks")
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
