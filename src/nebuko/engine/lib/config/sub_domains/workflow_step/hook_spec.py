"""Hook spec entity (issue #884)."""

from typing import Optional

from pydantic import BaseModel, model_validator


class HookSpec(BaseModel):
    """One deterministic hook command run in the session workspace (issue #884).

    A hook is declared on a workflow step under the ``pre_hook`` / ``post_hook``
    key; the key name is the phase discriminator, so the same spec shape serves
    both without a ``phase`` field — the same "nested object name is the kind"
    convention as ``ScriptStepSpec``. Validation mirrors ``ScriptStepSpec``: the
    path is repo-relative (no absolute paths or ``..`` traversal) and the
    timeout, when present, is a positive number of minutes.
    """

    path: str
    args: Optional[list[str]] = None
    timeout_minutes: Optional[int] = None

    @model_validator(mode="after")
    def _validate_hook_path(self) -> "HookSpec":
        # Repo-relative only: no absolute paths, no '..' path traversal.
        # Splitting on '/' means a literal filename like '..bar' stays valid.
        if self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError(
                "'hook.path' must be repo-relative (no leading '/' or '..')"
            )
        if self.timeout_minutes is not None and self.timeout_minutes < 1:
            raise ValueError(
                "'timeout_minutes' must be a positive integer (minutes)"
            )
        return self
