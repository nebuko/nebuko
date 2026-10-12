"""Deterministic step-hook execution (issue #884).

A workflow step may declare a ``pre_hook`` and/or a ``post_hook`` — repo-local
scripts the engine runs in the session workspace around the step's agent
invocation. :class:`HookRunner` is the narrow execution seam: it runs one
:class:`HookSpec` via :mod:`subprocess` with the run's environment and returns an
immutable :class:`HookResult`. A hook failure (non-zero exit, timeout, or a
failed exec) is *data*, never an exception — the caller decides how the phase's
policy (fail-closed ``pre`` / fail-open ``post``) reacts to it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Mapping, Optional

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from config import HookSpec

__all__ = ["HookPhase", "HookResult", "HookRunner"]

HookPhase = Literal["pre", "post"]

# Same documented contract as ``script.timeout_minutes``: default 30 minutes,
# hard-capped at 60 at execution time.
_DEFAULT_TIMEOUT_MINUTES = 30
_MAX_TIMEOUT_MINUTES = 60
_OUTPUT_TAIL_CHARS = 2000


@dataclass(frozen=True)
class HookResult:
    """Immutable outcome of one executed hook; no methods."""

    phase: HookPhase
    ok: bool
    exit_code: Optional[int]
    detail: str
    output_tail: str


def _timeout_seconds(spec: "HookSpec") -> int:
    minutes = spec.timeout_minutes or _DEFAULT_TIMEOUT_MINUTES
    return min(minutes, _MAX_TIMEOUT_MINUTES) * 60


def _command(spec: "HookSpec") -> list[str]:
    return [spec.path, *[str(arg) for arg in (spec.args or [])]]


def _tail(output: Optional[str]) -> str:
    text = output or ""
    if len(text) <= _OUTPUT_TAIL_CHARS:
        return text
    return text[-_OUTPUT_TAIL_CHARS:]


def _as_text(output: object) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


@dataclass(frozen=True)
class HookRunner:
    """Execution context for step hooks: where they run and with which env."""

    workspace: Path
    env: Mapping[str, str]

    def run(self, spec: "HookSpec", phase: HookPhase) -> HookResult:
        """Execute ``spec``; a failure or timeout is returned, never raised."""
        command = _command(spec)
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.workspace),
                env=dict(self.env),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=_timeout_seconds(spec),
            )
        except subprocess.TimeoutExpired as exc:
            return HookResult(
                phase=phase,
                ok=False,
                exit_code=None,
                detail=(
                    f"{phase}_hook '{spec.path}' timed out after "
                    f"{_timeout_seconds(spec) // 60} minute(s)"
                ),
                output_tail=_tail(_as_text(exc.stdout)),
            )
        except OSError as exc:
            return HookResult(
                phase=phase,
                ok=False,
                exit_code=None,
                detail=f"{phase}_hook '{spec.path}' could not be executed: {exc}",
                output_tail="",
            )
        ok = completed.returncode == 0
        return HookResult(
            phase=phase,
            ok=ok,
            exit_code=completed.returncode,
            detail=(
                f"{phase}_hook '{spec.path}' completed (exit 0)"
                if ok
                else f"{phase}_hook '{spec.path}' exited with code "
                f"{completed.returncode}"
            ),
            output_tail=_tail(completed.stdout),
        )
