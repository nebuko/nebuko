"""Session hook execution (issue #884).

A workflow step can declare ``pre_hook`` / ``post_hook`` — deterministic,
repo-local commands run in the session workspace immediately before the agent
is invoked and immediately after the agent attempt completes. This module owns
the execution seam: it decodes the resolver's ``STEP_HOOKS`` contract into
``HookSpec`` values, runs one hook as a bounded subprocess in the session
workspace, and returns an immutable ``HookResult``.

Hook failure is **data, not a crash**, mirroring ``DeterministicStep.execute``:
:meth:`HookRunner.run` never raises for a non-zero exit, a timeout, or an
exec-start error — it returns ``HookResult(ok=False, ...)`` for the caller to
act on (``pre_hook`` is fail-closed, ``post_hook`` is fail-open).
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

log = logging.getLogger("nebuko.engine.hook_runner")

#: Resolver env key carrying the JSON-encoded step-hook contract.
STEP_HOOKS_KEY = "STEP_HOOKS"

#: Hook phase discriminator: ``pre`` runs before the agent, ``post`` after it.
HookPhase = Literal["pre", "post"]

#: Wall-clock bounds applied to every hook (minutes): default when the spec
#: declares no ``timeout_minutes``; an upper cap otherwise — the same contract
#: as ``script.timeout_minutes``.
DEFAULT_TIMEOUT_MINUTES = 30
MAX_TIMEOUT_MINUTES = 60

#: Longest captured output retained on a ``HookResult`` (characters).
_OUTPUT_TAIL_CHARS = 2000


class HookPayloadError(Exception):
    """Raised on a malformed ``STEP_HOOKS`` payload (never on a hook failure)."""


@dataclass(frozen=True)
class HookResult:
    """Immutable outcome of executing one step hook."""

    phase: HookPhase
    ok: bool
    exit_code: Optional[int]  # None on timeout / exec-start failure
    detail: str  # human-readable outcome (e.g. "pre_hook exited with code 0")
    output_tail: str = ""  # trailing captured stdout/stderr


@dataclass(frozen=True)
class HookRunner:
    """Execution context for step hooks: where they run and with which env."""

    workspace: Path
    env: Mapping[str, str]

    def run(self, spec: Any, phase: HookPhase) -> HookResult:
        """Run ``spec`` in the session workspace; never raises.

        ``spec`` is a ``HookSpec`` (duck-typed here to keep this module
        import-light): ``path`` is resolved against the workspace, ``args`` are
        appended as an argv list (no shell interpolation), and the process is
        bounded by ``timeout_minutes`` (default 30, capped at 60).
        """
        timeout = _timeout_seconds(spec.timeout_minutes)
        argv = [*_command(spec, self.workspace)]
        started_detail = f"{phase}_hook"
        try:
            completed = subprocess.run(
                argv,
                cwd=str(self.workspace),
                env=dict(self.env),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return HookResult(
                phase=phase,
                ok=False,
                exit_code=None,
                detail=(
                    f"{started_detail} timed out after "
                    f"{timeout // 60}m"
                ),
                output_tail=_tail(_as_text(exc.output)),
            )
        except OSError as exc:
            return HookResult(
                phase=phase,
                ok=False,
                exit_code=None,
                detail=f"{started_detail} could not start: {exc}",
            )
        return HookResult(
            phase=phase,
            ok=completed.returncode == 0,
            exit_code=completed.returncode,
            detail=f"{started_detail} exited with code {completed.returncode}",
            output_tail=_tail(completed.stdout or ""),
        )


def hooks_from_resolved(
    resolved: Any,
) -> dict[HookPhase, Any]:
    """Decode the resolver's ``STEP_HOOKS`` contract into ``{phase: HookSpec}``.

    Returns an empty mapping when the step declares no hooks, so a hookless step
    keeps its resolver output and behaviour byte-identical. A malformed payload
    raises :class:`HookPayloadError` — silently dropping a declared ``pre_hook``
    would defeat its fail-closed guarantee.
    """
    if not isinstance(resolved, Mapping):
        return {}
    raw = resolved.get(STEP_HOOKS_KEY)
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HookPayloadError(
                f"invalid {STEP_HOOKS_KEY} payload: {exc}"
            ) from exc
    if not isinstance(raw, Mapping):
        raise HookPayloadError(f"{STEP_HOOKS_KEY} must decode to an object")

    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from config import HookSpec

    specs: dict[HookPhase, Any] = {}
    for phase in ("pre", "post"):
        payload = raw.get(phase)
        if payload:
            try:
                specs[phase] = HookSpec.model_validate(payload)
            except Exception as exc:  # noqa: BLE001 - a bad payload is terminal
                raise HookPayloadError(
                    f"invalid {phase}_hook in {STEP_HOOKS_KEY}: {exc}"
                ) from exc
    return specs


def _command(spec: Any, workspace: Path) -> tuple[str, ...]:
    """The argv to execute: the workspace-resolved script path plus its args."""
    script = Path(str(spec.path))
    if not script.is_absolute():
        script = workspace / script
    return (str(script), *(str(arg) for arg in (spec.args or [])))


def _timeout_seconds(timeout_minutes: Optional[int]) -> int:
    """The hook's wall-clock budget in seconds (default 30m, capped at 60m)."""
    minutes = timeout_minutes if timeout_minutes else DEFAULT_TIMEOUT_MINUTES
    return min(minutes, MAX_TIMEOUT_MINUTES) * 60


def _as_text(value: Any) -> str:
    """Normalize a possibly-bytes captured stream to ``str``."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else str(value)


def _tail(output: str) -> str:
    """The last :data:`_OUTPUT_TAIL_CHARS` characters of captured output."""
    output = output or ""
    return output[-_OUTPUT_TAIL_CHARS:] if len(output) > _OUTPUT_TAIL_CHARS else output
