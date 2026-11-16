"""Per-phase run timing (issue #1375).

The engine timed nothing, so engine-phase wall-clock (normalize … route,
including init) was only recoverable by parsing CI logs. ``PhaseClock`` is the
small in-process timer behind the ``timings.json`` artifact, the ``[timings]``
log summary and the failure notice's timing block.
"""

from __future__ import annotations

import pytest

from nebuko.engine.lib.run_timings import PhaseClock, PhaseRecord


class _Clock:
    """Deterministic monotonic clock, so no test has to sleep."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _clock() -> tuple[PhaseClock, _Clock]:
    source = _Clock()
    return PhaseClock(now=source), source


class TestPhaseRecord:
    def test_is_immutable(self):
        record = PhaseRecord(name="agent", duration_ms=1.0)

        with pytest.raises(Exception):
            record.name = "route"  # type: ignore[misc]


class TestPhaseClock:
    def test_records_a_measured_phase_in_milliseconds(self):
        clock, source = _clock()

        clock.start("agent")
        source.advance(1.5)
        clock.stop("agent")

        assert clock.summary() == {"agent": 1500.0}

    def test_start_closes_the_phase_still_running(self):
        """Only ``_enter`` marks boundaries, so a start must close the previous phase."""
        clock, source = _clock()

        clock.start("normalize")
        source.advance(0.25)
        clock.start("resolve_config")
        source.advance(0.5)
        clock.stop_current()

        assert clock.summary() == {"normalize": 250.0, "resolve_config": 500.0}

    def test_stop_of_an_unstarted_phase_is_a_no_op(self):
        clock, _ = _clock()

        clock.stop("route")

        assert clock.summary() == {}

    def test_stop_is_idempotent(self):
        clock, source = _clock()

        clock.start("agent")
        source.advance(1.0)
        clock.stop("agent")
        source.advance(1.0)
        clock.stop("agent")

        assert clock.summary() == {"agent": 1000.0}

    def test_summary_keeps_completion_order(self):
        clock, source = _clock()

        for name in ("normalize", "guards", "agent"):
            clock.start(name)
            source.advance(0.1)

        assert list(clock.summary()) == ["normalize", "guards"]

    def test_snapshot_includes_the_phase_still_running(self):
        """The failure notice renders mid-phase, so that phase must be visible."""
        clock, source = _clock()
        clock.start("normalize")
        source.advance(0.1)
        clock.start("agent")
        source.advance(2.0)

        assert clock.snapshot() == {"normalize": 100.0, "agent": 2000.0}
        # The running phase is not consumed: it still closes normally.
        clock.stop_current()
        assert clock.summary() == {"normalize": 100.0, "agent": 2000.0}

    def test_reset_forgets_everything(self):
        clock, source = _clock()
        clock.start("agent")
        source.advance(1.0)

        clock.reset()

        assert clock.summary() == {}
        clock.stop_current()
        assert clock.summary() == {}
