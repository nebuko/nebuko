"""Skill attachment entry: a delivery mode plus the skill names it applies to."""

from typing import Any, Iterator, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

INJECT = "inject"
DISCOVER = "discover"

_MODES = (INJECT, DISCOVER)


def _as_names(value: Any) -> Any:
    """Normalize a mode value to a name list; invalid shapes pass through."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return value


class SkillSpec(BaseModel):
    """One ``skills:``/``skill:`` entry (issue #955).

    Accepts the legacy bare string (``- planning``) and the mode-object shape
    (``- inject: [planning]`` / ``- discover: [complexity]``), normalizing both
    to ``mode`` + ``names``. A bare string is ``inject``: it is the mode the
    pre-#955 configs were documented as having. ``extra="forbid"`` so a typo
    inside an entry fails loudly at load instead of being dropped silently.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["inject", "discover"]
    names: list[str] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _coerce_entry(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"mode": INJECT, "names": [value]}
        if isinstance(value, dict) and "names" not in value:
            modes = [mode for mode in _MODES if mode in value]
            # Exactly one mode key and nothing else: anything more is a typo the
            # `extra="forbid"` contract must reject rather than silently drop.
            if len(modes) == 1 and len(value) == 1:
                return {"mode": modes[0], "names": _as_names(value[modes[0]])}
        return value

    @classmethod
    def parse_all(cls, raw: Optional[Sequence[Any]]) -> list["SkillSpec"]:
        """Parse a whole entry list, skipping entries that cannot be parsed."""
        specs: list[SkillSpec] = []
        for entry in raw or ():
            try:
                specs.append(cls.model_validate(entry))
            except ValueError:
                continue
        return specs

    @classmethod
    def iter_names(cls, raw: Any) -> Iterator[str]:
        """Walk the skill names of a raw (unvalidated) entry list.

        Tolerant by design: the linter must be able to report a malformed entry
        rather than crash on it.
        """
        for entry in raw or ():
            yield from _entry_names(entry)


def _entry_names(entry: Any) -> Iterator[str]:
    if isinstance(entry, str):
        yield entry
        return
    if isinstance(entry, SkillSpec):
        yield from entry.names
        return
    if not isinstance(entry, dict):
        return
    if "names" in entry:
        values = entry["names"]
    else:
        values = next((entry[mode] for mode in _MODES if mode in entry), None)
    if isinstance(values, str):
        yield values
    elif isinstance(values, (list, tuple)):
        for value in values:
            if isinstance(value, str):
                yield value
