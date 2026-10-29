"""Checkpoint schedule — ask the agent to save its thinking before the kill.

A single soft-kill timer loses everything the agent knows if it has not
written it down by the time the watchdog fires (issue #966). This module
replaces that single event with a schedule of checkpoints computed as
fractions of ``soft_timeout_s``, so ``AGENT_SOFT_TIMEOUT_MINUTES`` rescales
them automatically. With the default 20-minute budget the checkpoints sit at
10, 15 and 20 minutes.

Delivery: checkpoints 1 and 2 are *queued* — the message is sent to the
conversation without cancelling the in-flight ``arun()`` call, so the agent
receives it at the next turn boundary. Checkpoint 3 (100%) is the existing
soft-kill wind-down, now built on the handoff document.

Each checkpoint asks for ``plan_changes`` in the handoff, so comparing
versions shows whether the plan moved; the per-run count is logged as a
drift signal for later analysis.
"""

import re
import threading
from dataclasses import dataclass
from typing import Callable, Literal

from openhands.sdk import get_logger

from nebuko.engine.lib.agent_script.features.run_agent.wind_down_prompt import WIND_DOWN_PROMPT

logger = get_logger(__name__)


CheckpointLabel = Literal[
    "checkpoint_50", "checkpoint_75", "checkpoint_100"
]

#: (label, fraction of soft_timeout_s) — the documented 50/75/100% schedule.
CHECKPOINT_FRACTIONS: tuple[tuple[CheckpointLabel, float], ...] = (
    ("checkpoint_50", 0.5),
    ("checkpoint_75", 0.75),
    ("checkpoint_100", 1.0),
)

CHECKPOINT_50_PROMPT = """\
⏱️  CHECKPOINT 1 — HALFWAY THROUGH YOUR TIME BUDGET

Write your design and thinking so far by calling the handoff tool now:
concrete files touched, commands run, the next three actions, and open
questions. Then state plainly: are you converging on the goal? If not,
narrow the budget scope or fold (the handoff tool supports fold: true).

In the handoff's plan_changes field, record what changed versus your initial
plan (use "initial plan" if this is the first write). Be concrete; this
document is what a retry will start from.
"""

CHECKPOINT_75_PROMPT = """\
⏱️  CHECKPOINT 2 — 75% OF YOUR TIME BUDGET ELAPSED

Update the handoff document now. In plan_changes, state exactly what changed
since checkpoint 1 and why — a reviewer will diff the two versions. Then
make the call: finish cleanly within your remaining budget, or narrow the
scope to what is already working. Do not start new work that cannot land.
"""

# Checkpoint 3 is the existing wind-down, built on the handoff: the agent
# updates the document first (so a hard kill after this point still leaves a
# usable record), then commits and pushes.
CHECKPOINT_100_PROMPT = f"""\
⏱️  CHECKPOINT 3 — FINAL UPDATE BEFORE WIND-DOWN

{WIND_DOWN_PROMPT}

Do the handoff update FIRST (step 1 above becomes: call the handoff tool
with the final state), then commit and push. If you do not write, the
previous checkpoint's handoff plus the deterministic PR fallback carries on.
"""


CHECKPOINT_PROMPTS: dict[CheckpointLabel, str] = {
    "checkpoint_50": CHECKPOINT_50_PROMPT,
    "checkpoint_75": CHECKPOINT_75_PROMPT,
    "checkpoint_100": CHECKPOINT_100_PROMPT,
}


@dataclass(frozen=True)
class CheckpointEvent:
    """One scheduled checkpoint of the soft-time budget.

    Attributes:
        label: Which checkpoint this is (keys of ``CHECKPOINT_FRACTIONS``).
        offset_s: Seconds after run start when the checkpoint fires.
        prompt: The message delivered to the agent at the checkpoint.
    """

    label: CheckpointLabel
    offset_s: float
    prompt: str


def compute_checkpoints(soft_timeout_s: float) -> tuple[CheckpointEvent, ...]:
    """Compute the checkpoint schedule for a soft budget.

    Args:
        soft_timeout_s: The soft time budget in seconds. Must be positive.

    Returns:
        One :class:`CheckpointEvent` per entry of ``CHECKPOINT_FRACTIONS``,
        at the corresponding fraction of ``soft_timeout_s``.

    Raises:
        ValueError: If ``soft_timeout_s`` is not positive.
    """
    if soft_timeout_s <= 0:
        raise ValueError(
            f"soft_timeout_s must be positive, got {soft_timeout_s}"
        )
    return tuple(
        CheckpointEvent(
            label=label, offset_s=fraction * soft_timeout_s,
            prompt=CHECKPOINT_PROMPTS[label],
        )
        for label, fraction in CHECKPOINT_FRACTIONS
    )


class CheckpointScheduler:
    """Fire checkpoint messages on a background timer while a run is live.

    Delivery is queued: ``deliver`` is called on the timer thread (typically
    ``conversation.send_message``) without interrupting the in-flight
    ``arun()``. A checkpoint is skipped when the run has already finished, or
    once the scheduler has been cancelled — which the caller does when the
    soft watchdog fires or the run returns.
    """

    def __init__(
        self,
        checkpoints: tuple[CheckpointEvent, ...],
        deliver: Callable[[CheckpointEvent], None],
        is_finished: Callable[[], bool],
    ) -> None:
        self._checkpoints = checkpoints
        self._deliver = deliver
        self._is_finished = is_finished
        self._timers: list[threading.Timer] = []
        self._cancelled = False
        self.fired: list[CheckpointLabel] = []

    def arm(self) -> "CheckpointScheduler":
        """Schedule every checkpoint. No-op if already armed."""
        if self._timers:
            return self
        for event in self._checkpoints:
            timer = threading.Timer(
                event.offset_s, self._fire, args=(event,)
            )
            timer.daemon = True
            self._timers.append(timer)
            timer.start()
        return self

    def cancel(self) -> None:
        """Stop all pending checkpoints (idempotent)."""
        self._cancelled = True
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()

    def _fire(self, event: CheckpointEvent) -> None:
        if self._cancelled or self._is_finished():
            logger.info(
                f"Checkpoint {event.label} skipped "
                "(run finished or scheduler cancelled)"
            )
            return
        self.fired.append(event.label)
        logger.info(f"Checkpoint {event.label} firing at {event.offset_s:.0f}s")
        try:
            self._deliver(event)
        except Exception as e:  # noqa: BLE001 - a failed checkpoint must not kill the run
            logger.error(f"Checkpoint {event.label} delivery failed: {e}")


_PLAN_CHANGES_HEADING = "## Plan changes"


def count_plan_changes(handoff_text: str) -> int:
    """Count the non-empty ``plan_changes`` bullets in a handoff document.

    Bullets live under the ``## Plan changes`` heading until the next
    ``##`` heading (or end of document); both ``- item`` and rendered
    ordered-list items count.
    """
    lines: list[str] = []
    in_section = False
    for line in handoff_text.splitlines():
        if line.strip() == _PLAN_CHANGES_HEADING:
            in_section = True
            lines = []
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            lines.append(line)
    return sum(
        1
        for line in lines
        if re.match(r"\s*(?:-|\d+\.)\s+\S", line)
    )
