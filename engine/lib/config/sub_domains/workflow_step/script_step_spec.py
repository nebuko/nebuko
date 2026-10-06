"""Script step spec entity (issue #541)."""

from typing import Optional

from pydantic import BaseModel, model_validator


class ScriptStepSpec(BaseModel):
    """Configuration for a deterministic, non-agent script step (issue #541).

    The step source is the ``script`` key itself on ``WorkflowStep`` — the
    nested object name acts as the kind discriminator (no separate ``kind``
    field), consistent with the ``agent``/``workflow``/``repo`` source keys.
    """

    path: str
    args: Optional[list[str]] = None
    timeout_minutes: Optional[int] = None

    @model_validator(mode="after")
    def _validate_script_path(self) -> "ScriptStepSpec":
        # Repo-relative only: no absolute paths, no '..' path traversal.
        # Splitting on '/' means a literal filename like '..bar' stays valid.
        if self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError(
                "'script.path' must be repo-relative (no leading '/' or '..')"
            )
        if self.timeout_minutes is not None and self.timeout_minutes < 1:
            raise ValueError(
                "'timeout_minutes' must be a positive integer (minutes)"
            )
        return self