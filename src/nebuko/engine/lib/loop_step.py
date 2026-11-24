"""Step index of a step running inside the single-job loop (issue #1457).

The step driver exports the index before each in-process step so per-step
artifacts (metrics file, console/app logs, timings) keep one record per step
instead of overwriting each other. Unset outside the loop.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type hint only
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

LOOP_STEP_ENV = "WORKFLOW_LOOP_STEP"


def loop_step_index(env: Mapping[str, str] | None = None) -> int | None:
    """The 1-based loop step index, ``None`` outside the loop."""
    raw = (os.environ if env is None else env).get(LOOP_STEP_ENV, "")
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if value > 0 else None


def handed_over_state() -> "WorkflowState | None":
    """The state the previous loop step handed over in-process, if any.

    Only a later step (index > 1) reads it: the first step of a job must not
    adopt a hand-off left on a reused runner. Otherwise the posted comment
    stays the authority.
    """
    if (loop_step_index() or 1) <= 1:
        return None
    from nebuko.engine.lib.workflow_state.state_codec import WorkflowStateCodec  # noqa: PLC0415

    loaded = WorkflowStateCodec().load_prev()
    return loaded.state if loaded.status == "ok" else None
