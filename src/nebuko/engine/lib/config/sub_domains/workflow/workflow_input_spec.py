"""Declared workflow input entity (issue #813).

A workflow is a typed function: ``inputs`` are its parameters. This entity
declares one parameter — its name, its kind (``exec`` for a deterministic
provider command, ``str`` for a constant), and whether the caller must supply
it.

Wiring is validated by the linter against this signature: every declared input
must be bound exactly once at each call site, and a binding's kind must match
the declared kind (with ``ref`` bindings being kind-preserving).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .input_binding import InputBinding, InputMaterializationError

InputKind = Literal["exec", "str"]


class WorkflowInputSpec(BaseModel):
    """One typed input a workflow requires — a function parameter (issue #813)."""

    name: str
    kind: InputKind
    required: bool = True

    def materialize(self, binding: InputBinding, scope: dict[str, str]) -> str:
        """Resolve ``binding`` into this input's value.

        A literal ``str``/``exec`` binding whose kind contradicts the declared
        kind is rejected (the runtime twin of the linter's kind check); a
        ``ref`` binding is resolved through ``scope``. Raises
        :class:`InputMaterializationError` on any failure.
        """
        source = binding.source
        if source != "ref" and source != self.kind:
            raise InputMaterializationError(
                f"input '{self.name}' expects a '{self.kind}' binding, "
                f"got '{source}'"
            )
        return binding.resolve(scope)
