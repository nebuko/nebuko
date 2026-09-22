"""
SCAFFOLD_BRANCH marker handling (issue #604).

The scaffold step (``skills/scaffold-plan.md``) creates a feature branch
holding ``tmp_impl/workflow.yaml`` and reports the pushed branch name in
its final answer as a machine-readable marker::

    SCAFFOLD_BRANCH: feature/issue-590-port-adapter-engine

This module extracts that marker so the engine can persist the branch
name in the workflow state (``WorkflowState.scaffold_branch``), and
strips it from the posted answer so users never see the raw marker.

The marker contract targets the agent's "final answer", which in
practice is its **latest text answer** (issue #522) — while the engine
also inspects the short ``finish``-tool message (issue #636). Since the
two channels diverge by design, ``extract_scaffold_branch_any``
searches both, with fixed precedence: ``finish_message`` first, then
``latest_answer``. A malformed marker in the first source never blocks
a valid marker in the second.

Mirror of :mod:`agent_script.sub_domains.split_plan.split_plan`.
"""

import re
from dataclasses import dataclass
from typing import Optional

_MARKER_RE = re.compile(r"^[ \t]*SCAFFOLD_BRANCH:\s*(\S+)[ \t]*\r?\n?", re.MULTILINE)
# Reject anything that cannot be a ref fetched by the remote-workflow
# fetcher (whitespace is already excluded by \S; guard the rest).
_INVALID_REF_CHARS_RE = re.compile(r'["\'<>|]')


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
    if not value or value.startswith("-") or _INVALID_REF_CHARS_RE.search(value):
        return None
    return value


@dataclass(frozen=True)
class ScaffoldBranchResult:
    """Immutable outcome of the multi-text SCAFFOLD_BRANCH marker search."""

    branch: Optional[str]  # validated branch ref, or None
    source: Optional[str]  # "finish_message" | "latest_answer" | None


# @stub-protected: signature defined by architect — do not modify
def extract_scaffold_branch_any(
    final_message: str | None,
    latest_answer: str | None,
) -> ScaffoldBranchResult:
    """Find a valid ``SCAFFOLD_BRANCH`` marker across the run's terminal texts.

    Deterministic precedence: ``finish_message`` first (preserves the
    single-source behavior of issue #604), then ``latest_answer`` as
    fallback (the channel the scaffold-plan skill actually mandates the
    marker on). Reuses the existing marker regex and invalid-ref
    validation; a malformed marker in the first source never blocks a
    valid marker in the second.

    The function is pure (no logging) — the caller owns the warnings.

    Args:
        final_message: The ``finish``-tool message (SDK
            ``get_agent_final_response``).
        latest_answer: The agent's latest full text answer (issue #522).

    Returns:
        ``ScaffoldBranchResult(branch, source)`` with ``source`` set to
        ``"finish_message"`` or ``"latest_answer"``, or
        ``ScaffoldBranchResult(None, None)`` when no valid marker is
        present in either text.
    """
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
