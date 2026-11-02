"""Requirements ledger and plan coverage parsing (issue #1257).

The `analyze` step emits a `requirements` ledger (R/K/NG/Q markdown table)
and the `plan` step emits a `coverage` table referencing those IDs. This
module is the pure parsing/validation layer: it turns the section bodies
back into entries and refuses a plan whose coverage misses an enforced
(R/K) ledger ID. Enforcement is R/K-only; NG and Q rows are prompt-checked.
The check is fail-open: no ledger, or an unparseable one, is a no-op.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class LedgerKind(str, Enum):
    REQUIREMENT = "R"  # @stub-protected
    CONSTRAINT = "K"  # @stub-protected
    NON_GOAL = "NG"  # @stub-protected
    QUALITY = "Q"  # @stub-protected


LEDGER_ID_RE: re.Pattern[str] = re.compile(r"^(R|K|NG|Q)\d+$")  # type: ignore[has-type]


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: LedgerKind
    statement: str
    source: str
    origin: Literal["explicit", "inferred"]

    # @stub-protected: signature defined by architect — do not modify
    def _validate_id(self) -> None:
        """The id matches LEDGER_ID_RE and its prefix equals the kind value."""
        raise NotImplementedError


@dataclass
class RequirementsLedger:
    entries: list[LedgerEntry] = field(default_factory=list)

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def parse(cls, section_body: str) -> "RequirementsLedger":
        """Markdown table rows -> entries."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def ids(self) -> list[str]:
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def enforced_ids(self) -> list[str]:
        """R and K ids only."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def inferred_ids(self) -> list[str]:
        raise NotImplementedError


class CoverageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ledger_id: str
    covered_by: str
    verified_by: str
    deferred: Optional[str] = None


@dataclass
class CoverageTable:
    rows: list[CoverageRow] = field(default_factory=list)

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def parse(cls, section_body: str) -> "CoverageTable":
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def covered_ids(self) -> set[str]:
        """Non-deferred ids."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def deferred_ids(self) -> set[str]:
        raise NotImplementedError


class CoverageValidationError(ValueError):
    def __init__(self, missing_ids: list[str]) -> None:
        # @stub-protected: signature defined by architect — do not modify
        raise NotImplementedError


# @stub-protected: signature defined by architect — do not modify
def validate_coverage(ledger: RequirementsLedger, coverage: CoverageTable) -> None:
    """Raise CoverageValidationError for enforced_ids not covered and not deferred."""
    raise NotImplementedError


class PlanCoverageGuard:
    # @stub-protected: signature defined by architect — do not modify
    def check(self, coverage_body: str, ledger_body: Optional[str]) -> None:
        """No-op when ledger_body is None or unparseable (fail-open); otherwise
        parse both and call validate_coverage; CoverageValidationError
        propagates to the caller for same-turn refusal."""
        raise NotImplementedError
