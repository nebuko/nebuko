"""Deterministic step-input materialization (issue #813).

A workflow is a typed function: its declared ``inputs`` are parameters, supplied
by the caller at a ``workflow`` call site and consumed by downstream ``agent``
steps through ``input.<name>`` / ``<step>`` references. This module turns
the resolver's ``STEP_INPUTS`` payload into fresh, engine-injected context:

* **frame bindings** (the live sub-workflow call's parameters) are resolved
  first, in declaration order, so an earlier input can feed a later one;
* **consumer bindings** (the current step's ``inputs:``) are resolved against
  that scope and *forced* — an ``exec`` binding runs its command here, at the
  prompt-assembly seam, not at the call boundary, so artifacts are fresh no
  matter how many workflow layers the parameter was threaded through;
* the resulting labeled blocks are injected into the step context, making the
  model's inputs engine-guaranteed rather than model-discretionary.

``subprocess`` and the config entities are imported lazily/duck-typed so this
module stays import-light and root-cause free from the resolver's bootstrap.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .models import WorkflowCallFrame

log = logging.getLogger("nebuko.engine.input_wiring")

#: Resolver env key carrying the JSON-encoded step-input contract.
STEP_INPUTS_KEY = "STEP_INPUTS"

#: Default wall-clock budget for a materialized ``exec`` provider command.
EXEC_TIMEOUT_SECONDS = 600

#: Maximum number of nested sub-workflow call frames a single run may carry.
#: Kind-preserving lazy forwarding lets a parameter cross an arbitrary number
#: of layers, so the frame chain needs the same explicit bound the resolver's
#: leaf descent already has (``_MAX_SUB_WORKFLOW_DEPTH``) — a too-deep chain
#: fails the step deterministically instead of forwarding unboundedly.
MAX_CALL_FRAMES = 5


class InputWiringError(Exception):
    """Raised on a malformed ``STEP_INPUTS`` payload (never on a provider failure)."""


@dataclass(frozen=True)
class Materialization:
    """Immutable outcome of resolving one step input."""

    name: str
    ok: bool
    body: str
    detail: str = ""
    # Binding kind that produced the value ("exec"/"str"/"ref"; issue #872), so a
    # persisted ``StepInputRecord`` can say how the value was wired.
    binding: str = ""


@dataclass(frozen=True)
class _Deferred:
    """An unmaterialized ``exec`` command, forwarded lazily until forced."""

    argv: tuple[str, ...]


@dataclass(frozen=True)
class StepInputs:
    """Decoded ``STEP_INPUTS`` payload: call frames + this step's own bindings."""

    frames: tuple[WorkflowCallFrame, ...] = ()
    consumer: Mapping[str, Any] = field(default_factory=dict)
    specs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_resolved(cls, resolved: Any) -> "StepInputs | None":
        """Parse the payload; ``None`` when the step declares no inputs."""
        raw = _get(resolved, STEP_INPUTS_KEY)
        if not raw:
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise InputWiringError(f"invalid {STEP_INPUTS_KEY} payload: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise InputWiringError(f"{STEP_INPUTS_KEY} must decode to an object")
        return cls(
            frames=_decode_frames(raw.get("frames") or []),
            consumer=_decode_bindings(raw.get("consumer") or {}),
            specs={
                str(name): dict(spec)
                for name, spec in (raw.get("specs") or {}).items()
            },
        )

    def required_failures(
        self, results: list[Materialization]
    ) -> list[Materialization]:
        """The failed inputs whose declared spec forbids degradation."""
        return [
            result
            for result in results
            if not result.ok and bool(self.specs.get(result.name, {}).get("required", True))
        ]

    def depth_exceeded(self) -> bool:
        """True when the call-frame chain nests deeper than :data:`MAX_CALL_FRAMES`."""
        return len(self.frames) > MAX_CALL_FRAMES


def _get(resolved: Any, key: str) -> Any:
    if isinstance(resolved, Mapping):
        return resolved.get(key)
    return getattr(resolved, key, None)


def _decode_frames(raw: Any) -> tuple[WorkflowCallFrame, ...]:
    frames: list[WorkflowCallFrame] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        frames.append(
            WorkflowCallFrame(
                callee=str(entry.get("callee") or ""),
                repo_ref=str(entry.get("repo_ref") or ""),
                call_step=str(entry.get("call_step") or ""),
                bindings=_decode_bindings(entry.get("bindings") or {}),
            )
        )
    return tuple(frames)


def _decode_bindings(raw: Any) -> dict[str, Any]:
    from nebuko.engine.lib.config import InputBinding

    bindings: dict[str, Any] = {}
    for name, spec in raw.items():
        bindings[str(name)] = InputBinding.model_validate(spec)
    return bindings


def _resolve_binding(binding: Any, scope: Mapping[str, Any]) -> Any:
    """Resolve one binding to a bare ``str`` or a :class:`_Deferred` command."""
    source = getattr(binding, "source", None)
    if source == "exec":
        return _Deferred(tuple(binding.exec_ or ()))
    if source == "ref":
        ref = str(binding.ref)
        if ref not in scope:
            from nebuko.engine.lib.config import InputMaterializationError

            raise InputMaterializationError(f"unresolved reference '{ref}'")
        return scope[ref]
    return str(binding.str_ if binding.str_ is not None else "")


def _force(value: Any) -> str:
    """Materialize a resolved value: run a deferred command, else return text."""
    if isinstance(value, _Deferred):
        completed = subprocess.run(
            list(value.argv),
            capture_output=True,
            text=True,
            timeout=EXEC_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip() or f"exit code {completed.returncode}"
            raise InputWiringError(detail)
        return completed.stdout
    return str(value)


# @stub-protected
def materialize(
    step_inputs: StepInputs,
    step_scope: Mapping[str, str] | None = None,
) -> list[Materialization]:
    """Resolve frame bindings then consumer bindings; never raises for a provider.

    A failing provider (non-zero exit, timeout, unresolved reference) yields a
    failed :class:`Materialization`; the caller applies the ``required`` policy.

    ``step_scope`` maps upstream step names to their state-stripped comment
    bodies (issue #756): a consumer binding whose ``ref`` is a bare step name
    resolves against it. ``input.<name>`` frame parameters take precedence.
    """
    scope: dict[str, Any] = {}
    for frame in step_inputs.frames:
        for name, binding in frame.bindings.items():
            scope[f"input.{name}"] = _resolve_binding(binding, scope)
    for step, body in (step_scope or {}).items():
        scope.setdefault(step, body)

    results: list[Materialization] = []
    for name, binding in step_inputs.consumer.items():
        kind = _binding_kind(binding)
        try:
            body = _force(_resolve_binding(binding, scope))
            results.append(
                Materialization(name=name, ok=True, body=body, binding=kind)
            )
        except Exception as exc:  # noqa: BLE001 - a provider failure is data
            results.append(
                Materialization(
                    name=name, ok=False, body="", detail=str(exc), binding=kind
                )
            )
    return results


def _binding_kind(binding: Any) -> str:
    """The binding's source key ("exec"/"str"/"ref"), or "" when undeterminable."""
    return str(getattr(binding, "source", "") or "")


# @stub-protected
def render(results: list[Materialization]) -> str:
    """Render materialized inputs as labeled context blocks for the prompt."""
    if not results:
        return ""
    blocks: list[str] = []
    for result in results:
        if result.ok:
            blocks.append(f"#### `{result.name}`\n\n{result.body}")
        else:
            blocks.append(
                f"#### `{result.name}`\n\n_(unavailable: {result.detail})_"
            )
    return "## Step inputs (deterministic)\n\n" + "\n\n".join(blocks)


def to_records(results: Sequence[Materialization]) -> list[dict[str, Any]]:
    """Serialize this step's materializations for the workflow state (issue #872).

    Returns JSON-friendly dicts (a ``StepInputRecord`` per materialized input) so
    the advancing step's ``HistoryEntry`` can record what was wired to it. The
    value is truncated to the entity's cap (a large provider output must not
    balloon the state snapshot); the entity owns the cap so the two never drift.
    """

    from nebuko.engine.lib.workflow_state.sub_domains.step_input_record.step_input_record import (
        MAX_VALUE_CHARS,
    )

    records: list[dict[str, Any]] = []
    for result in results:
        value = result.body or ""
        records.append(
            {
                "name": result.name,
                "binding": result.binding,
                "ok": result.ok,
                "detail": result.detail,
                "value": value[:MAX_VALUE_CHARS],
                "truncated": len(value) > MAX_VALUE_CHARS,
            }
        )
    return records
