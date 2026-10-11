"""Input binding entity (issue #813).

One caller-supplied value for a declared workflow input, wired at a call site.
A binding declares exactly one source key — the same nested-key discrimination
``WorkflowStep`` uses for its ``agent``/``workflow``/``repo``/``script``
sources:

- ``exec`` — a literal command (argv list) run deterministically to capture
  context (the mandatory pre-hook the caller asked for);
- ``str``  — a constant string;
- ``ref``  — a reference (``input.<name>`` or a bare ``<step>``) resolved
  against the caller's frame scope.

A bare string is shorthand for a ``ref`` binding, so ``name: input.foo``
reads cleanly without a flow mapping. A bare step name (``plan``) references
that step's posted comment (issue #756).

``resolve`` is the side-effecting edge of the wiring: every other derivation
in the feature is a pure function over the frozen values it returns.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ``input.<name>`` (a frame parameter) or a bare ``<step>`` (an upstream step
# whose posted comment is the value; issue #756).
REF_PATTERN = re.compile(
    r"^(?:input\.[A-Za-z_][A-Za-z0-9_-]*|[A-Za-z_][A-Za-z0-9_-]*)$"
)

# The pre-#756 ``<step>.<out>`` grammar, kept only to emit a migration hint.
_LEGACY_DOTTED_REF_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_-]*\.[A-Za-z_][A-Za-z0-9_-]*$"
)

_SOURCE_ATTRS = {"exec": "exec_", "str": "str_", "ref": "ref"}

BindingSource = Literal["exec", "str", "ref"]


class InputMaterializationError(RuntimeError):
    """A binding could not be materialized (exec failure or unresolved ref)."""


class InputBinding(BaseModel):
    """One caller-supplied value for a declared workflow input (issue #813)."""

    model_config = ConfigDict(populate_by_name=True)

    exec_: Optional[list[str]] = Field(default=None, alias="exec")
    str_: Optional[str] = Field(default=None, alias="str")
    ref: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_string(cls, data: Any) -> Any:
        """Treat a bare string as a ``ref`` binding (block-style shorthand)."""
        if isinstance(data, str):
            return {"ref": data}
        return data

    @model_validator(mode="after")
    def _validate_single_source(self) -> "InputBinding":
        present = [k for k, attr in _SOURCE_ATTRS.items() if getattr(self, attr) is not None]
        if len(present) != 1:
            raise ValueError(
                "InputBinding must declare exactly one of 'exec', 'str', or 'ref'"
            )
        if self.ref is not None and not REF_PATTERN.match(self.ref):
            if _LEGACY_DOTTED_REF_PATTERN.match(self.ref):
                raise ValueError(
                    f"InputBinding 'ref' {self.ref!r} uses the removed "
                    "'<step>.<out>' grammar (issue #756); reference the step "
                    "name ('<step>') instead"
                )
            raise ValueError(
                "InputBinding 'ref' must match the 'input.<name>' or '<step>' "
                f"grammar, got {self.ref!r}"
            )
        return self

    @property
    def source(self) -> BindingSource:
        """Which source key this binding declares."""
        for kind, attr in _SOURCE_ATTRS.items():
            if getattr(self, attr) is not None:
                return kind  # type: ignore[return-value]
        raise InputMaterializationError("binding declares no source")

    def resolve(self, scope: dict[str, str]) -> str:
        """Materialize this binding against ``scope``.

        ``str`` returns its constant; ``ref`` returns the value registered
        under the reference (a ``KeyError`` is surfaced as a typed
        :class:`InputMaterializationError`); ``exec`` runs the command and
        returns its captured stdout.
        """
        if self.str_ is not None:
            return self.str_
        if self.ref is not None:
            if self.ref not in scope:
                raise InputMaterializationError(
                    f"unresolved reference {self.ref!r} (not bound in scope)"
                )
            return scope[self.ref]
        if self.exec_ is not None:
            return _capture_exec(self.exec_, self.ref)
        raise InputMaterializationError("binding declares no source")


def _capture_exec(argv: list[str], _ref: Optional[str] = None) -> str:
    """Run ``argv`` and return its stdout; raise on non-zero exit."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        raise InputMaterializationError(f"command {argv!r} failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        raise InputMaterializationError(
            f"command {argv!r} exited with code {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout
