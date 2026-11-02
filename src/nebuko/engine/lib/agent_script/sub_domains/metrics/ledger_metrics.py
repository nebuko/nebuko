"""Ledger metrics model and collector (issue #1257)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from nebuko.engine.ledger import RequirementsLedger


class LedgerMetrics(BaseModel):
    ledger_size: int
    inferred_entries: int
    coverage_refusals: int


class LedgerMetricsCollector:
    # @stub-protected: signature defined by architect — do not modify
    def collect(
        self, ledger: Optional[RequirementsLedger], coverage_refusals: int
    ) -> LedgerMetrics:
        return LedgerMetrics(
            ledger_size=len(ledger.entries) if ledger is not None else 0,
            inferred_entries=(
                len(ledger.inferred_ids()) if ledger is not None else 0
            ),
            coverage_refusals=coverage_refusals,
        )
