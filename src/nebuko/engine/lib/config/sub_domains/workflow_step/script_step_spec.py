"""Script step spec entity (issue #541)."""

import json
from typing import Optional

from pydantic import BaseModel, model_validator


class ScriptStepSpec(BaseModel):
    """Configuration for a deterministic, non-agent script step (issue #541).

    The step source is the ``script`` key itself on ``WorkflowStep`` — the
    nested object name acts as the kind discriminator (no separate ``kind``
    field), consistent with the ``agent``/``workflow``/``repo`` source keys.

    Field naming follows the Kubernetes ``command``/``args``/``workingDir``
    contract (issue #974): ``command`` is the executable, ``args`` its argv.
    Nothing is wrapped in a shell: the process is spawned as
    ``[command, *args]``, so an entry containing spaces or quotes survives
    verbatim.
    """

    command: str
    args: Optional[list[str]] = None
    working_dir: Optional[str] = None
    env: Optional[dict[str, str]] = None
    timeout_minutes: Optional[int] = None

    @model_validator(mode="after")
    def _validate_script_source(self) -> "ScriptStepSpec":
        if not self.command:
            raise ValueError("'script.command' must be a non-empty command")
        # Repo-relative only: no absolute paths, no '..' path traversal.
        # Splitting on '/' means a literal filename like '..bar' stays valid.
        if self.working_dir is not None and (
            self.working_dir.startswith("/") or ".." in self.working_dir.split("/")
        ):
            raise ValueError(
                "'script.working_dir' must be repo-relative "
                "(no leading '/' or '..')"
            )
        if self.timeout_minutes is not None and self.timeout_minutes < 1:
            raise ValueError(
                "'timeout_minutes' must be a positive integer (minutes)"
            )
        return self

    def to_process_env(self) -> dict[str, str]:
        """The resolver env contract for this spec (issue #974).

        ``SCRIPT_ARGS`` is a JSON array of the argv entries and ``SCRIPT_ENV`` a
        JSON object, so both round-trip losslessly through the environment (the
        pre-#974 space-join/``shlex.split`` pair did not). ``SCRIPT_WORKING_DIR``
        stays repo-relative — the executor anchors it at the session workspace
        root.
        """
        return {
            "SCRIPT_ARGS": json.dumps(list(self.args or [])),
            "SCRIPT_WORKING_DIR": self.working_dir or "",
            "SCRIPT_ENV": json.dumps(dict(self.env or {})),
        }