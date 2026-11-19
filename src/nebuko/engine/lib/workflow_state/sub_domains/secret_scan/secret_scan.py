"""Secret-scan facts recorded on the workflow state (issue #1347).

Agents paste raw tool output (CI logs, env dumps) into commits and comments, and
the repository is public. The engine scans both before they leave the sandbox
and records *what* it found here — never the value: only the file, the line and
the rule name. The next comment renders a short warning from this record, and
the field persists across transitions so a later step cannot re-introduce the
value silently.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["SecretScanStatus", "SecretFinding", "SecretScan"]


class SecretScanStatus(str, Enum):  # @stub-protected
    """Outcome of the latest scan: nothing found, or values were redacted."""

    CLEAN = "clean"
    REDACTED = "redacted"


class SecretFinding(BaseModel):  # @stub-protected
    """One redacted value, by location and rule — never by value.

    Fields:
        file: Path the value was found in (``""`` for a comment body).
        line: 1-based line number inside ``file``.
        rule: Scanner rule that matched (e.g. ``github-oauth``).
    """

    model_config = ConfigDict(extra="forbid")

    file: str = ""
    line: int = 0
    rule: str = ""


class SecretScan(BaseModel):  # @stub-protected
    """Persisted secret-scan record for a workflow run.

    Fields:
        status: ``clean`` when nothing matched, ``redacted`` once a value was
            replaced (the status never goes back to ``clean`` for the run).
        findings: Every redaction, oldest first, deduplicated by location+rule.
        at: Step the scan ran in (``""`` when unknown).
    """

    model_config = ConfigDict(extra="forbid")

    status: SecretScanStatus = SecretScanStatus.CLEAN
    findings: list[SecretFinding] = Field(default_factory=list)
    at: str = ""

    def merge(
        self, findings: list[SecretFinding], at: Optional[str] = None
    ) -> "SecretScan":  # @stub-protected
        """Return this record extended with ``findings`` (deduplicated).

        A redaction is permanent for the run: once ``status`` is ``redacted``
        it stays that way, so a later clean scan cannot hide an earlier one.
        """
        known = {(f.file, f.line, f.rule) for f in self.findings}
        merged = list(self.findings)
        for finding in findings:
            key = (finding.file, finding.line, finding.rule)
            if key in known:
                continue
            known.add(key)
            merged.append(finding)
        status = self.status
        if merged:
            status = SecretScanStatus.REDACTED
        return SecretScan(
            status=status,
            findings=merged,
            at=at if at is not None else self.at,
        )
