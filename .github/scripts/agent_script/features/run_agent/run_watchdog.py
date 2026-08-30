"""Watchdog — supervises a bounded conversation run (issue #489).

Fires a callback once when the soft time budget elapses, so the caller can
call ``conversation.interrupt()`` (cancels the in-flight ``arun()`` task,
including mid-LLM-call). Pure standard library; unit-testable with short
delays.
"""

import threading

from openhands.sdk import get_logger

logger = get_logger(__name__)


class RunWatchdog:
    """One-shot timer that invokes a callback once the budget elapses.

    The callback is expected to interrupt the in-flight run. After firing,
    :attr:`fired` is set so the caller can tell a watchdog interrupt apart
    from a natural completion.
    """

    def __init__(self, delay_s: float, on_fire) -> None:
        self._delay_s = delay_s
        self._on_fire = on_fire
        self._timer: threading.Timer | None = None
        self.fired = False

    @property
    def delay_s(self) -> float:
        return self._delay_s

    def arm(self) -> "RunWatchdog":
        """Start the countdown. No-op if already armed."""
        if self._timer is not None:
            return self
        self._timer = threading.Timer(self._delay_s, self._fire)
        self._timer.daemon = True
        self._timer.start()
        return self

    def cancel(self) -> None:
        """Stop the countdown if still pending."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _fire(self) -> None:
        self.fired = True
        try:
            self._on_fire()
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Watchdog on_fire callback failed: {e}")