"""
SCAFFOLD_BRANCH marker handling (issue #604).

The scaffold step (``skills/scaffold-plan.md``) creates a feature branch
holding ``tmp_impl/workflow.yaml`` and reports the pushed branch name in
its final answer as a machine-readable marker::

    SCAFFOLD_BRANCH: feature/issue-590-port-adapter-engine

This module extracts that marker so the engine can persist the branch
name in the workflow state (``WorkflowState.scaffold_branch``), and
strips it from the posted answer so users never see the raw marker.

Mirror of :mod:`agent_script.sub_domains.split_plan.split_plan`.
"""

import re

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
