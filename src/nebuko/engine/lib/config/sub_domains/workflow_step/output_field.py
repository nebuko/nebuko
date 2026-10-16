"""Declared step-output DSL (issue #973).

A step may declare the shape of its posted comment as an ordered list of
fields. The declaration is the single source for three derived artifacts:

* the section prompt block the agent writes its answer against
  (:meth:`OutputContract.prompt_block`);
* the render/validate spec the engine applies at the terminal ``finalize``
  gate (:class:`OutputRenderer`);
* the route-only ``response_schema`` the finalize tool exposes.

The DSL is deliberately small: a field is a named section of the comment, and
the engine — never the model — owns the markdown wrappers (``#`` title, ``##``
heading, ``<details>``) so extraction can return the model's body verbatim.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

#: Field and column names: lowercase, digits and underscores, starting with a
#: letter. A dot would break ``step.field`` reference parsing; a space or
#: ``-->`` would break the section marker.
NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

_NAME_RE = re.compile(NAME_PATTERN)


class OutputField(BaseModel):
    """One declared section of a step's posted comment."""

    name: str = Field(pattern=NAME_PATTERN)
    type: Literal["text", "table"] = "text"
    description: str = ""
    #: Rendered ``##`` heading; empty falls back to the name with its initial
    #: letter capitalized.
    heading: str = ""
    #: When true this field's body is the comment's ``#`` lead-in. At most one
    #: field per output may set it, and only a ``text`` field may.
    title: bool = False
    #: When true the body is wrapped in ``<details>`` (the wrapper stays
    #: outside the section markers).
    collapsed: bool = False
    #: When false the field may be absent from the agent's answer; the renderer
    #: then emits no heading and no markers for it.
    required: bool = True
    #: Column names for a ``table`` field (same name pattern, unique, non-empty
    #: iff ``type == "table"``).
    columns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_field(self) -> "OutputField":
        if self.type == "table":
            if not self.columns:
                raise ValueError(
                    f"output field '{self.name}': a 'table' field requires 'columns'"
                )
        elif self.columns:
            raise ValueError(
                f"output field '{self.name}': 'columns' is only valid on a "
                "'table' field"
            )
        for column in self.columns:
            if not _NAME_RE.match(column):
                raise ValueError(
                    f"output field '{self.name}': column '{column}' must match "
                    f"{NAME_PATTERN}"
                )
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(
                f"output field '{self.name}': column names must be unique"
            )
        if self.title and self.type != "text":
            raise ValueError(
                f"output field '{self.name}': 'title' is only valid on a "
                "'text' field"
            )
        return self

    @property
    def rendered_heading(self) -> str:
        """The ``##`` heading text: ``heading`` or the capitalized name."""
        if self.heading:
            return self.heading
        return self.name[:1].upper() + self.name[1:]


class StepOutput(BaseModel):
    """A step's declared output: an ordered list of comment sections."""

    fields: list[OutputField]

    @model_validator(mode="after")
    def _validate_output(self) -> "StepOutput":
        names = [field.name for field in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("output field names must be unique")
        if sum(1 for field in self.fields if field.title) > 1:
            raise ValueError("at most one output field may set 'title: true'")
        return self
