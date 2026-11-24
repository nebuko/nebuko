"""Step index of a step running inside the single-job loop (issue #1457).

The step driver exports the index before each in-process step so per-step
artifacts (metrics file, console/app logs, timings) keep one record per step
instead of overwriting each other. Unset outside the loop.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

LOOP_STEP_ENV = "WORKFLOW_LOOP_STEP"


def loop_step_index(env: Mapping[str, str] | None = None) -> int | None:
    """The 1-based loop step index, ``None`` outside the loop."""
    raw = (os.environ if env is None else env).get(LOOP_STEP_ENV, "")
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if value > 0 else None
