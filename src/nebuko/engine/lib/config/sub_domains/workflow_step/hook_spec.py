"""Step hook spec entity (issue #884)."""

from typing import Optional

from pydantic import BaseModel, model_validator


class HookSpec(BaseModel):
    """One deterministic hook command run in the session workspace (issue #884).

    The hook's YAML key on ``WorkflowStep`` is the discriminator: ``pre_hook:``
    runs before the agent is invoked, ``post_hook:`` after the agent attempt.
    The shape mirrors ``ScriptStepSpec`` (repo-relative ``path``, argv ``args``,
    ``timeout_minutes``) but the two contracts differ in failure semantics — a
    failing script step advances the workflow, while a failing ``pre_hook``
    must stop the agent.
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
