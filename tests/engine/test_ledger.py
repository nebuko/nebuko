"""Tests for the requirements ledger and plan coverage guard (issue #1257).

Red tests: they lock the Object Design Contract stubs and are expected to
fail (NotImplementedError) until the stubs are filled in.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from nebuko.engine.ledger import (
    CoverageTable,
    CoverageValidationError,
    LEDGER_ID_RE,
    LedgerKind,
    LedgerEntry,
    PlanCoverageGuard,
    RequirementsLedger,
    validate_coverage,
)

FIXTURES = Path(__file__).parent / "ledger_fixtures"


def _requirements_body() -> str:
    return (FIXTURES / "requirements_full.md").read_text()


def _coverage_body() -> str:
    return (FIXTURES / "coverage_full.md").read_text()


def test_ledger_id_re_accepts_all_four_kinds():
    for ledger_id in ("R1", "K2", "NG3", "Q1"):
        assert LEDGER_ID_RE.match(ledger_id)


def test_ledger_id_re_rejects_bad_ids():
    for bad in ("X1", "r1", "R", "R1x", "1R", ""):
        assert not LEDGER_ID_RE.match(bad)


def test_parse_reads_all_four_kinds():
    ledger = RequirementsLedger.parse(_requirements_body())
    kinds = {entry.id: entry.kind for entry in ledger.entries}
    assert kinds["R1"] is LedgerKind.REQUIREMENT
    assert kinds["K1"] is LedgerKind.CONSTRAINT
    assert kinds["NG1"] is LedgerKind.NON_GOAL
    assert kinds["Q1"] is LedgerKind.QUALITY


def test_entry_id_must_match_kind_prefix():
    with pytest.raises(ValidationError):
        LedgerEntry(
            id="R1",
            kind=LedgerKind.CONSTRAINT,
            statement="one atomic statement",
            source="quote",
            origin="explicit",
        )


def test_enforced_ids_are_r_and_k_only():
    ledger = RequirementsLedger.parse(_requirements_body())
    assert ledger.enforced_ids() == ["R1", "R2", "R3", "K1"]
    assert "Q1" in ledger.inferred_ids()
    assert "Q1" not in ledger.enforced_ids()


def test_coverage_parses_rows():
    coverage = CoverageTable.parse(_coverage_body())
    assert coverage.covered_ids() >= {"R1", "R2", "R3", "K1"}


def test_validate_coverage_accepts_full_coverage():
    ledger = RequirementsLedger.parse(_requirements_body())
    validate_coverage(ledger, CoverageTable.parse(_coverage_body()))


def test_validate_coverage_raises_missing_ids():
    ledger = RequirementsLedger.parse(_requirements_body())
    body = _coverage_body().replace("| R3 |", "| R3x |")
    with pytest.raises(CoverageValidationError) as exc_info:
        validate_coverage(ledger, CoverageTable.parse(body))
    assert str(exc_info.value) == "coverage missing: K2, R3"


def test_deferred_row_exempts():
    # Deferral is per-ID: R3 is deferred, every other enforced id is covered.
    ledger = RequirementsLedger.parse(_requirements_body())
    from nebuko.engine.ledger import CoverageRow

    coverage = CoverageTable(
        rows=[
            CoverageRow(
                ledger_id=ledger_id,
                covered_by="deferred: lives in the future lint rule" if ledger_id == "R3" else "plan step",
                verified_by="none" if ledger_id == "R3" else "test",
                deferred="lives in the future lint rule" if ledger_id == "R3" else None,
            )
            for ledger_id in ("R1", "R2", "R3", "K1", "K2")
        ]
    )
    validate_coverage(ledger, coverage)


def test_deferred_row_does_not_exempt_other_ids():
    ledger = RequirementsLedger.parse(_requirements_body())
    from nebuko.engine.ledger import CoverageRow

    coverage = CoverageTable(
        rows=[
            CoverageRow(
                ledger_id="R3",
                covered_by="",
                verified_by="none",
                deferred="lives in the future lint rule",
            )
        ]
    )
    with pytest.raises(CoverageValidationError) as exc_info:
        validate_coverage(ledger, coverage)
    assert str(exc_info.value) == "coverage missing: K1, K2, R1, R2"


def test_parse_reads_deferred_marker():
    coverage = CoverageTable.parse(
        "| ID | Covered by | Verified by |\n|---|---|---|\n"
        "| R3 | deferred: future lint rule | — |\n| R1 | plan step | test |\n"
    )
    assert coverage.deferred_ids() == {"R3"}
    assert coverage.covered_ids() == {"R1"}
    assert coverage.rows[0].deferred == "future lint rule"


def test_guard_noop_without_ledger():
    PlanCoverageGuard().check(_coverage_body(), None)


def test_guard_noop_with_unparseable_ledger():
    PlanCoverageGuard().check(_coverage_body(), "not a ledger table")


def test_guard_refuses_missing_enforced_id():
    # K1 becomes a valid but uncovered enforced id: the guard must refuse.
    ledger_body = _requirements_body().replace("| K1 |", "| K9 |")
    with pytest.raises(CoverageValidationError):
        PlanCoverageGuard().check(_coverage_body(), ledger_body)


def test_ledger_metrics_collector():
    from nebuko.engine.lib.agent_script.sub_domains.metrics.ledger_metrics import (
        LedgerMetricsCollector,
    )

    metrics = LedgerMetricsCollector().collect(
        RequirementsLedger.parse(_requirements_body()), coverage_refusals=2
    )
    assert metrics.ledger_size == 7
    assert metrics.inferred_entries == 1
    assert metrics.coverage_refusals == 2
