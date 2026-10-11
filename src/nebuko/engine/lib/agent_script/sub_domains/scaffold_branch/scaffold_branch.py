"""
SCAFFOLD_BRANCH marker handling (issue #604).

The scaffold step (``skills/scaffold-plan.md``) creates a feature branch
holding ``tmp_impl/workflow.yaml`` and reports the pushed branch name in
its final answer as a machine-readable marker::

    SCAFFOLD_BRANCH: feature/issue-590-port-adapter-engine

This module extracts that marker so the engine can persist the branch
name in the workflow state (``WorkflowState.scaffold_branch``), and
strips it from the posted answer so users never see the raw marker.

The primary channel is the ``report_scaffold_branch`` tool (issue #636,
rework): the scaffold agent calls it right after pushing, and the engine
reads the validated ref from the run-scoped registry — a structured,
first-class hand-off that never touches prose.

As a legacy fallback for in-flight or older runs, the marker contract
targets the agent's "final answer", which in practice is its **latest
text answer** (issue #522) — while the engine also inspects the short
``finish``-tool message. Since the two channels diverge by design,
``extract_scaffold_branch_any`` searches both, with fixed precedence:
``tool`` first, then ``finish_message``, then ``latest_answer``. A
malformed marker in one source never blocks a valid marker in another.
"""

import re
from dataclasses import dataclass
from typing import Optional

_MARKER_RE = re.compile(r"^[ \t]*SCAFFOLD_BRANCH:\s*(\S+)[ \t]*\r?\n?", re.MULTILINE)
# Reject anything that cannot be a ref fetched by the remote-workflow
# fetcher (whitespace is already excluded by \S; guard the rest).
_INVALID_REF_CHARS_RE = re.compile(r'["\'<>|]')


# @stub-protected: signature defined by architect — do not modify
def validate_scaffold_branch_ref(value: Optional[str]) -> bool:
    """Return True when ``value`` is a usable scaffold-branch ref.

    Pure predicate factored out of the marker-path validation so the
    ``report_scaffold_branch`` tool and the legacy ``SCAFFOLD_BRANCH``
    marker parsing share one rule set.

    Args:
        value: Candidate branch ref.

    Returns:
        True when the ref is non-empty, does not start with ``-``, and
        contains no git-hostile characters (``['"<>|]``); False otherwise
        (including ``None``).
    """
    if not value:
        return False
    if value.startswith("-"):
        return False
    return _INVALID_REF_CHARS_RE.search(value) is None


def extract_scaffold_branch(message: str | None) -> str | None:
    """Extract the branch name from a ``SCAFFOLD_BRANCH: <ref>`` marker.

    Args:
        message: The agent's final answer text.

    Returns:
        The branch/ref name, or ``None`` when no well-formed marker is
        present (absent, or malformed value).
    """
    if not message:
        return None
    match = _MARKER_RE.search(message)
    if not match:
        return None
    value = match.group(1).strip()
    if not validate_scaffold_branch_ref(value):
        return None
    return value


@dataclass(frozen=True)
class ScaffoldBranchResult:
    """Immutable outcome of the multi-source SCAFFOLD_BRANCH search."""

    branch: Optional[str]  # validated branch ref, or None
    source: Optional[str]  # "tool" | "finish_message" | "latest_answer" | None


# @stub-protected: signature defined by architect — do not modify
def extract_scaffold_branch_any(
    final_message: str | None,
    latest_answer: str | None,
    tool_branch: str | None = None,
) -> ScaffoldBranchResult:
    """Resolve the scaffold branch across the run's structured + text sources.

    Deterministic precedence: ``tool_branch`` first (the value the
    ``report_scaffold_branch`` tool recorded — already validated at call
    time), then the ``finish_message`` marker (preserves the single-source
    behavior of issue #604), then the ``latest_answer`` marker as fallback
    (the channel the scaffold-plan skill historically mandated the marker
    on). A malformed marker in one source never blocks a valid value in
    another.

    The function is pure (no logging) — the caller owns the warnings.

    Args:
        final_message: The ``finish``-tool message (SDK
            ``get_agent_final_response``).
        latest_answer: The agent's latest full text answer (issue #522).
        tool_branch: Ref recorded by the ``report_scaffold_branch`` tool
            (issue #636 rework), or None when the tool was not called.

    Returns:
        ``ScaffoldBranchResult(branch, source)`` with ``source`` set to
        ``"tool"``, ``"finish_message"``, or ``"latest_answer"``, or
        ``ScaffoldBranchResult(None, None)`` when no valid source is
        present.
    """
    if tool_branch:
        return ScaffoldBranchResult(branch=tool_branch, source="tool")
    branch = extract_scaffold_branch(final_message)
    if branch is not None:
        return ScaffoldBranchResult(branch=branch, source="finish_message")
    branch = extract_scaffold_branch(latest_answer)
    if branch is not None:
        return ScaffoldBranchResult(branch=branch, source="latest_answer")
    return ScaffoldBranchResult(branch=None, source=None)


def strip_scaffold_branch(message: str | None) -> str | None:
    """Remove the ``SCAFFOLD_BRANCH`` marker line from a message.

    Trailing whitespace left by the removal is trimmed; a message without
    the marker is returned unchanged.
    """
    if not message:
        return message
    return _MARKER_RE.sub("", message).rstrip()


def scaffold_branch_summary(branch: str) -> str:
    """Render a one-line human-facing summary for a set branch name."""
    return f"Scaffold branch: `{branch}`"
