"""Single-job driver for the user-repo lane (issue #1457).

While the lane is ``user-repo`` the whole workflow runs in one job: each step
is a fresh :class:`Engine` (and a fresh adapter, so the thread snapshot is
re-read), and the driver decides after every step whether the next one may
start. The decision is the pure :class:`StopPolicy`; the driver only sequences
steps, resets process state and posts the notices for stops it causes itself.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from nebuko.engine.lib.loop_step import LOOP_STEP_ENV
from nebuko.engine.models import ExitCode, RunOutcome

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from nebuko.engine.run_loop import Engine

log = logging.getLogger(__name__)

_DEFAULT_MAX_STEPS = 6
# The org token lives one hour; the budget keeps the job inside it (issue #1457).
_DEFAULT_WALL_CLOCK_S = 50 * 60.0
_DEFAULT_STEP_BUDGET_S = 20 * 60.0
_TRUE_VALUES = ("1", "true", "yes")
_FALSE_VALUES = ("0", "false", "no")


class StopReason(Enum):
    """Why the driver stopped before the workflow reached its own end."""

    NOT_ADVANCING = "not_advancing"  # engine error / refusal / provider failure
    HOLD = "hold"  # human gate or awaiting_input hold: the engine already said so
    TERMINAL = "terminal"  # terminal step: the engine already said so
    HUMAN_COMMENT = "human_comment"  # a human posted on the issue during the run
    STEP_CAP = "step_cap"
    WALL_CLOCK = "wall_clock"  # next step would outlive the org token
    TOKEN_EXPIRED = "token_expired"  # the thread probe was rejected (401)
    PROBE_FAILED = "probe_failed"  # thread unreadable: cannot rule out a human comment


# Stops the driver causes itself; every other stop is announced by the engine.
_DRIVER_NOTICE_REASONS = frozenset(
    {
        StopReason.HUMAN_COMMENT,
        StopReason.STEP_CAP,
        StopReason.WALL_CLOCK,
        StopReason.TOKEN_EXPIRED,
        StopReason.PROBE_FAILED,
    }
)


@dataclass(frozen=True)
class DriverConfig:
    """Opt-in and budgets for single-job mode (env ``WORKFLOW_*``)."""

    enabled: bool
    max_steps: int = _DEFAULT_MAX_STEPS
    # Token life fallback: the step budget must fit before this elapsed mark.
    wall_clock_s: float = _DEFAULT_WALL_CLOCK_S
    step_budget_s: float = _DEFAULT_STEP_BUDGET_S

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        lane: str | None,
        event: str,
        payload: Mapping[str, object] | None = None,
    ) -> "DriverConfig":
        """On for the user-repo lane on a dispatch; ``WORKFLOW_SINGLE_JOB`` overrides.

        A dispatch with ``client_payload.single_step`` (or ``WORKFLOW_SINGLE_STEP``)
        runs exactly one step, as before the loop existed.
        """
        forced = str(env.get("WORKFLOW_SINGLE_JOB", "")).strip().lower()
        if forced in _TRUE_VALUES:
            enabled = True
        elif forced in _FALSE_VALUES:
            enabled = False
        else:
            enabled = lane == "user-repo" and event == "repository_dispatch"
        single_step = bool((payload or {}).get("single_step")) or (
            str(env.get("WORKFLOW_SINGLE_STEP", "")).strip().lower() in _TRUE_VALUES
        )
        if single_step:
            enabled = False
        return cls(
            enabled=enabled,
            max_steps=_positive_int(env.get("WORKFLOW_MAX_STEPS"), _DEFAULT_MAX_STEPS),
            wall_clock_s=_positive_float(
                env.get("WORKFLOW_WALL_CLOCK_S"), _DEFAULT_WALL_CLOCK_S
            ),
            step_budget_s=_step_budget_s(env),
        )


@dataclass(frozen=True)
class BoundaryContext:
    """What the policy knows at a step boundary."""

    step_index: int  # 1-based number of the step that just finished
    elapsed_s: float
    comment_ids_at_start: frozenset[str] = frozenset()
    comment_ids_now: frozenset[str] = frozenset()

    @property
    def new_human_comments(self) -> frozenset[str]:
        return self.comment_ids_now - self.comment_ids_at_start


class StopPolicy:
    """Pure stop decision; the driver calls it once per boundary."""

    def __init__(self, cfg: DriverConfig) -> None:
        self._cfg = cfg

    def decide(self, outcome: RunOutcome, ctx: BoundaryContext) -> StopReason | None:
        """``None`` when the next step may start in this job."""
        code = ExitCode(outcome.exit_code)
        if code is ExitCode.HOLD:
            return StopReason.HOLD
        if code is ExitCode.TERMINAL:
            return StopReason.TERMINAL
        if code is not ExitCode.ADVANCE:
            return StopReason.NOT_ADVANCING
        if ctx.new_human_comments:
            return StopReason.HUMAN_COMMENT
        if ctx.step_index >= self._cfg.max_steps:
            return StopReason.STEP_CAP
        if ctx.elapsed_s + self._cfg.step_budget_s > self._cfg.wall_clock_s:
            return StopReason.WALL_CLOCK
        return None


class StepDriver:
    """Runs steps in one process until :class:`StopPolicy` says stop."""

    def __init__(
        self,
        cfg: DriverConfig,
        engine_factory: Callable[[int], "Engine"],
        adapter_probe: Callable[[], frozenset[str]],
        clock: Callable[[], float],
        notify: Callable[[str], None],
    ) -> None:
        self._cfg = cfg
        self._policy = StopPolicy(cfg)
        self._engine_factory = engine_factory
        self._probe = adapter_probe
        self._clock = clock
        self._notify = notify
        self._env_snapshot: dict[str, str] = {}
        self.step_timings: list[dict[str, float]] = []

    def run(self) -> RunOutcome:
        """Run the workflow; returns the outcome of the last step run."""
        if not self._cfg.enabled:
            # Outside the loop the step runs exactly as before: same env, same token.
            return self._run_step(1, looping=False)
        self._env_snapshot = dict(os.environ)
        try:
            return self._run_loop()
        finally:
            # The driver's own exports must not outlive it (in-process callers).
            os.environ.clear()
            os.environ.update(self._env_snapshot)

    def _run_loop(self) -> RunOutcome:
        started = self._clock()
        try:
            baseline = frozenset(self._probe())
        except Exception:  # noqa: BLE001 - no baseline, no loop: one step only
            log.warning("[driver] thread probe failed; running one step", exc_info=True)
            return self._run_step(1, looping=False)
        index = 1
        outcome = self._run_step(index, looping=True)
        while True:
            elapsed = self._clock() - started
            reason, notice_ctx = self._boundary(outcome, index, elapsed, baseline)
            if reason is None:
                index += 1
                outcome = self._run_step(index, looping=True)
                continue
            if reason in _DRIVER_NOTICE_REASONS:
                self._post_stop_notice(reason, notice_ctx)
            return outcome

    def _boundary(
        self, outcome: RunOutcome, index: int, elapsed: float, baseline: frozenset[str]
    ) -> tuple[StopReason | None, "_NoticeContext"]:
        """Decide the stop at a boundary; probes the thread only after an ADVANCE."""
        notice = _NoticeContext(step_index=index, elapsed_s=elapsed)
        now = baseline
        if ExitCode(outcome.exit_code) is ExitCode.ADVANCE:
            try:
                now = frozenset(self._probe())
            except Exception as exc:  # noqa: BLE001 - classify, then stop
                return (
                    StopReason.TOKEN_EXPIRED
                    if _is_auth_error(exc)
                    else StopReason.PROBE_FAILED
                ), notice
        ctx = BoundaryContext(
            step_index=index,
            elapsed_s=elapsed,
            comment_ids_at_start=baseline,
            comment_ids_now=now,
        )
        return self._policy.decide(outcome, ctx), notice

    def _run_step(self, index: int, *, looping: bool) -> RunOutcome:
        if looping:
            self._reset_process_state(index)
        outcome = self._engine_factory(index).run(); print("DBG", index, outcome.exit_code, getattr(outcome, "turn_outcome", None), file=__import__("sys").stderr)
        timings = getattr(outcome, "timings", None)
        if isinstance(timings, Mapping):
            self.step_timings.append({"step": index, **timings})
        if looping and outcome.state is not None:
            # The next step reads this hand-off instead of the posted comment.
            from nebuko.engine.lib.workflow_state.state_codec import WorkflowStateCodec

            WorkflowStateCodec().save_prev(outcome.state)
        return outcome

    def _reset_process_state(self, index: int) -> None:
        """Undo what the previous step exported; keep the continue token off the loop."""
        os.environ.clear()
        os.environ.update(self._env_snapshot)
        os.environ["WORKFLOW_NO_CONTINUE"] = "1"
        os.environ[LOOP_STEP_ENV] = str(index)
        from nebuko.engine.lib.resolve_workflow import step_prompt_path

        Path(step_prompt_path()).unlink(missing_ok=True)

    def _post_stop_notice(self, reason: StopReason, ctx: "_NoticeContext") -> None:
        from nebuko.engine.lib.prepare_comment import BOT_MARKER

        text = _NOTICE_TEXT[reason].format(step=ctx.step_index, minutes=ctx.elapsed_s / 60)
        # The marker keeps the notice out of the trigger path; no continue token.
        try:
            self._notify(f"{BOT_MARKER}\n{text}")
        except Exception:  # noqa: BLE001 - a notice must not fail the run
            log.error("[driver] could not post the %s notice", reason.value, exc_info=True)


@dataclass(frozen=True)
class _NoticeContext:
    step_index: int
    elapsed_s: float


_NOTICE_TEXT: dict[StopReason, str] = {
    StopReason.HUMAN_COMMENT: (
        "Single-job run paused after step {step}: a new human comment on the issue "
        "was not acted on. Continue with `/nebuko resume` (or `/nebuko approve gate` "
        "at a gate), or dispatch a `step`."
    ),
    StopReason.STEP_CAP: (
        "Single-job run stopped after step {step} (step cap reached). Continue with "
        "`/nebuko resume` or a `step` dispatch."
    ),
    StopReason.WALL_CLOCK: (
        "Token expiring, re-dispatch: the single-job run stopped after step {step} "
        "({minutes:.0f} min) so the next step does not outlive the credential. "
        "Continue with `/nebuko resume`."
    ),
    StopReason.TOKEN_EXPIRED: (
        "Token expiring, re-dispatch: the credential was rejected before step "
        "{step} + 1 started. Continue with `/nebuko resume`."
    ),
    StopReason.PROBE_FAILED: (
        "Single-job run stopped after step {step}: the issue thread could not be read "
        "to check for new human comments. Re-dispatch with `/nebuko resume`."
    ),
}


def _is_auth_error(exc: BaseException) -> bool:
    """True for an HTTP 401 from the API (GitHub's body says "Bad credentials")."""
    response = getattr(exc, "response", None)
    status = getattr(exc, "status", None) or getattr(response, "status_code", None)
    return status == 401 or "Bad credentials" in str(exc)


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float(raw: str | None, default: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _step_budget_s(env: Mapping[str, str]) -> float:
    """One step's worst case: the engine's soft run plus its wind-down (same source)."""
    from nebuko.engine.lib.agent_script.features.run_agent.timeout_budget import (
        resolve_timeout_budget,
    )

    budget = resolve_timeout_budget(dict(env))
    return budget.soft_timeout_s + budget.wind_down_timeout_s


def read_dispatch_payload(env: Mapping[str, str]) -> dict[str, object]:
    """``client_payload`` of the event file, ``{}`` when absent or unreadable."""
    path = env.get("GITHUB_EVENT_PATH", "")
    if not path:
        return {}
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    payload = event.get("client_payload") if isinstance(event, dict) else None
    return dict(payload) if isinstance(payload, dict) else {}


__all__ = [
    "BoundaryContext",
    "DriverConfig",
    "StepDriver",
    "StopPolicy",
    "StopReason",
    "read_dispatch_payload",
]
