"""Secret scanning for what leaves the sandbox (issue #1347)."""

from .scanner import (
    REDACTION_PREFIX,
    Finding,
    is_evidence_path,
    redact_text,
    redact_worktree,
    scan_git_range,
    scan_text,
)

__all__ = [
    "REDACTION_PREFIX",
    "Finding",
    "is_evidence_path",
    "redact_text",
    "redact_worktree",
    "scan_git_range",
    "scan_text",
]
