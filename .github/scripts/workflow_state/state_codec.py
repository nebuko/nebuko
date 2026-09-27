"""Canonical ``WorkflowState`` codec and disk hand-off (issue #671).

The trigger side serialises the resolved previous state to ``RUNNER_TEMP`` so
the agent step can carry ``version``/``history`` forward; the agent side reads
it back via :meth:`WorkflowStateCodec.load_prev`. Keeping both ends on one
lossless codec removes the writer/reader asymmetry that silently dropped the
history in issue #648 — the runtime rebuilt state from ``prev_state=None``
(``version=3`` with a single history entry) although the trigger side had
resolved ``version=7`` and written it to ``prev_workflow_state.json``.
"""

import json
import os
from dataclasses import dataclass
from typing import Literal

from .features.to_json.to_json import state_to_json
from .workflow_state import WorkflowState

HANDOFF_FILENAME = "prev_workflow_state.json"


@dataclass(frozen=True)
class PrevStateLoad:
    """Outcome of loading the previous state from disk (issue #671).

    Attributes:
        state: The decoded state, or ``None`` when absent/invalid.
        status: ``"ok"`` when loaded, ``"absent"`` when no hand-off file
            exists, ``"invalid"`` when the file exists but cannot be decoded.
        detail: Human-readable failure reason (empty when ``status == "ok"``).
    """

    state: WorkflowState | None  # @stub-protected
    status: Literal["ok", "absent", "invalid"]  # @stub-protected
    detail: str = ""  # @stub-protected


class WorkflowStateCodec:
    """Single lossless codec plus ``RUNNER_TEMP`` hand-off for workflow state.

    Args:
        runner_temp: Directory holding the ``prev_workflow_state.json``
            hand-off file. When ``None``, ``$RUNNER_TEMP`` is read lazily at
            call time (falling back to ``/tmp``).
    """

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, runner_temp: str | None = None) -> None:
        self._runner_temp = runner_temp

    def _handoff_path(self) -> str:
        base = self._runner_temp or os.environ.get("RUNNER_TEMP", "/tmp")
        return os.path.join(base, HANDOFF_FILENAME)

    # @stub-protected: signature defined by architect — do not modify
    def encode(self, state: WorkflowState, *, compact: bool = False) -> str:
        """Serialise ``state`` to the canonical (optionally compact) JSON."""
        return state_to_json(state, compact=compact)

    # @stub-protected: signature defined by architect — do not modify
    def decode(self, raw: str) -> WorkflowState:
        """Parse canonical JSON back into a lossless ``WorkflowState``."""
        return WorkflowState.model_validate(json.loads(raw))

    # @stub-protected: signature defined by architect — do not modify
    def save_prev(self, state: WorkflowState) -> str:
        """Persist ``state`` to the hand-off file and return its path."""
        path = self._handoff_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.encode(state, compact=True))
        return path

    # @stub-protected: signature defined by architect — do not modify
    def load_prev(self) -> PrevStateLoad:
        """Load the hand-off state, distinguishing absent from invalid."""
        path = self._handoff_path()
        if not os.path.isfile(path):
            return PrevStateLoad(
                state=None,
                status="absent",
                detail=f"no previous workflow state hand-off at {path}",
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            return PrevStateLoad(
                state=None, status="invalid", detail=f"cannot read {path}: {e}"
            )
        try:
            return PrevStateLoad(state=self.decode(raw), status="ok")
        except Exception as e:
            return PrevStateLoad(
                state=None, status="invalid", detail=f"cannot decode {path}: {e}"
            )
