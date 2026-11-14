"""Per-phase wall-clock timing for one engine run (issue #1375).

The engine emitted one ``[phase:<name>]`` log marker per phase but timed
nothing, so engine-phase time (normalize … route, including init) was only
recoverable by parsing CI logs. :class:`PhaseClock` is the small in-process
timer behind the ``timings.json`` artifact and the ``[timings]`` log summary.

It is deliberately dependency-free and pure: the clock source is injected, so
the derivation is unit-testable without sleeping.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PhaseRecord:
    """One completed phase: its name and measured duration in milliseconds."""

    name: str
    duration_ms: float


class PhaseClock:
    """Start/stop timer for the engine's named phases.

    ``start`` closes whatever phase is still open, so a caller that only marks
    phase entries cannot leave a dangling start behind. ``stop`` is idempotent
    for an already-closed (or never-started) name.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._starts: dict[str, float] = {}
        self._records: list[PhaseRecord] = []
        self._current: str | None = None

    def start(self, name: str) -> None:
        """Open ``name``, closing the phase that was still running."""
        self.stop_current()
        self._starts[name] = self._now()
        self._current = name

    def stop(self, name: str) -> None:
        """Close ``name`` and record its duration; a no-op when not open."""
        started = self._starts.pop(name, None)
        if started is None:
            return
        self._records.append(
            PhaseRecord(
                name=name, duration_ms=round((self._now() - started) * 1000.0, 2)
            )
        )
        if self._current == name:
            self._current = None

    def stop_current(self) -> None:
        """Close the phase still running, if any."""
        if self._current is not None:
            self.stop(self._current)

    def summary(self) -> dict[str, float]:
        """Completed phases as ``{name: duration_ms}``, in completion order."""
        return {record.name: record.duration_ms for record in self._records}

    def snapshot(self) -> dict[str, float]:
        """Like :meth:`summary`, but also counts the phase still running.

        The failure notice is rendered *during* the phase that failed, so the
        phase's elapsed time must be visible without closing it (issue #1375).
        """
        timings = self.summary()
        if self._current is not None and self._current in self._starts:
            timings[self._current] = round(
                (self._now() - self._starts[self._current]) * 1000.0, 2
            )
        return timings

    def reset(self) -> None:
        """Forget every start and record, for a reused engine."""
        self._starts.clear()
        self._records.clear()
        self._current = None
