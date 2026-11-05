"""Requirements ledger and plan coverage parsing (issue #1257).

The `analyze` step emits a `requirements` ledger (R/K/NG/Q markdown table)
and the `plan` step emits a `coverage` table referencing those IDs. This
module is the pure parsing/validation layer: it turns the section bodies
back into entries and refuses a plan whose coverage misses an enforced
(R/K) ledger ID. Enforcement is R/K-only; NG and Q rows are prompt-checked.
The check is fail-open: no ledger, or an unparseable one, is a no-op. A ledger
section that has content but yields no rows logs a warning, so a check that
tested nothing is visible. The analyze step cannot be asked to redo its table
from the plan step, so it does not fail the plan.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

logger = logging.getLogger(__name__)

_LEDGER_MIN_COLUMNS = 4
_LEDGER_KIND_COLUMNS = 5
_COVERAGE_COLUMNS = 3


def _parse_table_rows(section_body: str, width: int) -> list[list[str]]:
    """Extract data rows from a markdown table; returns [] when absent."""
    rows: list[list[str]] = []
    for line in section_body.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != width or set(cells[0]) <= {"-", ":"} or cells[0] == "ID":
            continue
        rows.append(cells)
    return rows


class LedgerKind(str, Enum):
    REQUIREMENT = "R"  # @stub-protected
    CONSTRAINT = "K"  # @stub-protected
    NON_GOAL = "NG"  # @stub-protected
    QUALITY = "Q"  # @stub-protected


_KIND_BY_COLUMN: dict[str, LedgerKind] = {
    "requirement": LedgerKind.REQUIREMENT,
    "constraint": LedgerKind.CONSTRAINT,
    "non-goal": LedgerKind.NON_GOAL,
    "quality goal": LedgerKind.QUALITY,
}


LEDGER_ID_RE: re.Pattern[str] = re.compile(r"^(R|K|NG|Q)\d+$")  # type: ignore[has-type]


def _ledger_rows(section_body: str) -> list[list[str]]:
    """Table rows whose first cell is a ledger ID, whatever the column count.

    The step contract asks for an "R/K/NG/Q table with source and origin" and
    names no columns, so the kind column is optional: the ID prefix carries it.
    """
    rows: list[list[str]] = []
    for line in section_body.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= _LEDGER_MIN_COLUMNS and LEDGER_ID_RE.match(cells[0]):
            rows.append(cells)
    return rows


def _kind_of(ledger_id: str) -> LedgerKind:
    """The kind an ID's prefix names (``NG1`` -> non-goal)."""
    return LedgerKind(re.sub(r"\d+$", "", ledger_id))


def _origin_of(cell: str) -> str:
    """Normalize the origin cell, which agents fill with a login or a note.

    ``explicit``/``inferred`` stand as written. Anything else is ``explicit``
    (the row names who asked for it) unless it says it was inferred or derived.
    """
    lowered = cell.lower()
    return "inferred" if "infer" in lowered or "derived" in lowered else "explicit"


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
        if not LEDGER_ID_RE.match(self.id) or not self.id.startswith(self.kind.value):
            raise ValueError(f"ledger id {self.id!r} does not match kind {self.kind.value!r}")

    @model_validator(mode="after")
    def _check_id(self) -> "LedgerEntry":
        self._validate_id()
        return self


@dataclass
class RequirementsLedger:
    entries: list[LedgerEntry] = field(default_factory=list)

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def parse(cls, section_body: str) -> "RequirementsLedger":
        """Markdown table rows -> entries.

        Reads ``ID | Statement | Source | Origin`` and the same table with a
        ``Kind`` column second. A declared kind must agree with the ID prefix.
        """
        parsed: list[LedgerEntry] = []
        for cells in _ledger_rows(section_body):
            declared = (
                _KIND_BY_COLUMN.get(cells[1].lower())
                if len(cells) == _LEDGER_KIND_COLUMNS
                else None
            )
            statement = cells[2] if declared is not None else " | ".join(cells[1:-2])
            parsed.append(
                LedgerEntry(
                    id=cells[0],
                    kind=declared or _kind_of(cells[0]),
                    statement=statement,
                    source=cells[-2],
                    origin=_origin_of(cells[-1]),  # type: ignore[arg-type]
                )
            )
        return cls(entries=parsed)

    # @stub-protected: signature defined by architect — do not modify
    def ids(self) -> list[str]:
        return [entry.id for entry in self.entries]

    # @stub-protected: signature defined by architect — do not modify
    def enforced_ids(self) -> list[str]:
        """R and K ids only."""
        return [
            entry.id
            for entry in self.entries
            if entry.kind in (LedgerKind.REQUIREMENT, LedgerKind.CONSTRAINT)
        ]

    # @stub-protected: signature defined by architect — do not modify
    def inferred_ids(self) -> list[str]:
        return [entry.id for entry in self.entries if entry.origin == "inferred"]


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
        """A row defers its id with an in-cell `deferred: <reason>` marker in
        the `Covered by` cell, e.g. `| R3 | deferred: future lint rule | — |`."""
        rows = []
        for cells in _parse_table_rows(section_body, _COVERAGE_COLUMNS):
            ledger_id = cells[0].strip()
            if not LEDGER_ID_RE.match(ledger_id):
                continue
            covered_by = cells[1].strip()
            deferred: Optional[str] = None
            marker = re.match(r"deferred:\s*(.+)$", covered_by, re.IGNORECASE)
            if marker:
                deferred = marker.group(1).strip()
                covered_by = ""
            rows.append(
                CoverageRow(
                    ledger_id=ledger_id,
                    covered_by=covered_by,
                    verified_by=cells[2].strip(),
                    deferred=deferred,
                )
            )
        return cls(rows=rows)

    # @stub-protected: signature defined by architect — do not modify
    def covered_ids(self) -> set[str]:
        """Non-deferred ids."""
        return {row.ledger_id for row in self.rows if row.deferred is None}

    # @stub-protected: signature defined by architect — do not modify
    def deferred_ids(self) -> set[str]:
        return {row.ledger_id for row in self.rows if row.deferred is not None}


class CoverageValidationError(ValueError):
    def __init__(self, missing_ids: list[str]) -> None:
        # @stub-protected: signature defined by architect — do not modify
        self.missing_ids = missing_ids
        super().__init__(f"coverage missing: {', '.join(missing_ids)}")


# @stub-protected: signature defined by architect — do not modify
def validate_coverage(ledger: RequirementsLedger, coverage: CoverageTable) -> None:
    """Raise CoverageValidationError for enforced_ids not covered and not deferred."""
    missing = [
        ledger_id
        for ledger_id in ledger.enforced_ids()
        if ledger_id not in coverage.covered_ids()
        and ledger_id not in coverage.deferred_ids()
    ]
    # Deferral is per-ID: a deferred row exempts only its own ledger id,
    # never ids absent from the table.
    if missing:
        raise CoverageValidationError(sorted(missing))


class PlanCoverageGuard:
    # @stub-protected: signature defined by architect — do not modify
    def check(self, coverage_body: str, ledger_body: Optional[str]) -> None:
        """No-op when ledger_body is None or unparseable (fail-open); otherwise
        parse both and call validate_coverage; CoverageValidationError
        propagates to the caller for same-turn refusal."""
        if ledger_body is None:
            return
        try:
            ledger = RequirementsLedger.parse(ledger_body)
            coverage = CoverageTable.parse(coverage_body)
        except Exception:
            logger.warning("unparseable ledger or coverage section; coverage check skipped")
            return
        if ledger_body.strip() and not ledger.entries:
            logger.warning("requirements section has no ledger rows; coverage check skipped")
            return
        validate_coverage(ledger, coverage)
