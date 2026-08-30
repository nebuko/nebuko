"""Timeout budget resolution — soft budgets for graceful agent interruption.

The GitHub Actions ``timeout-minutes`` hard-kills the agent step. These soft
budgets let the process interrupt the in-flight LLM call at a safe point and
run a bounded wind-down phase instead of losing all context (issue #489).

Knobs (minutes, env → default):
  - AGENT_SOFT_TIMEOUT_MINUTES  (default 110): budget for the main run.
  - WIND_DOWN_TIMEOUT_MINUTES   (default 8):   budget for the wind-down run.
"""

import os
from dataclasses import dataclass

from openhands.sdk import get_logger

logger = get_logger(__name__)

DEFAULT_SOFT_TIMEOUT_MINUTES = 110
DEFAULT_WIND_DOWN_TIMEOUT_MINUTES = 8


@dataclass(frozen=True)
class TimeoutBudget:
    """Immutable soft time budgets (seconds) for agent execution."""

    soft_timeout_s: float
    wind_down_timeout_s: float


def _parse_minutes(raw: str | None, default: int, label: str) -> float:
    """Parse a minutes value from env, falling back to ``default``."""
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        logger.warning(f"Invalid {label} value '{raw}'; using default {default}")
        return float(default)
    if value <= 0:
        logger.warning(f"Non-positive {label} value '{value}'; using default {default}")
        return float(default)
    return value


def resolve_timeout_budget(
    env: dict | None = None,
) -> TimeoutBudget:
    """Resolve soft timeout budgets from environment variables.

    Args:
        env: Environment mapping (defaults to os.environ).

    Returns:
        TimeoutBudget with budgets converted to seconds.
    """
    source = os.environ if env is None else env
    soft_min = _parse_minutes(
        source.get("AGENT_SOFT_TIMEOUT_MINUTES"),
        DEFAULT_SOFT_TIMEOUT_MINUTES,
        "AGENT_SOFT_TIMEOUT_MINUTES",
    )
    wind_down_min = _parse_minutes(
        source.get("WIND_DOWN_TIMEOUT_MINUTES"),
        DEFAULT_WIND_DOWN_TIMEOUT_MINUTES,
        "WIND_DOWN_TIMEOUT_MINUTES",
    )
    budget = TimeoutBudget(
        soft_timeout_s=soft_min * 60,
        wind_down_timeout_s=wind_down_min * 60,
    )
    logger.info(
        f"Resolved timeout budget: soft={budget.soft_timeout_s:.0f}s, "
        f"wind_down={budget.wind_down_timeout_s:.0f}s"
    )
    return budget