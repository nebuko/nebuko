"""Prompt plan: the ordered intermediate representation of an agent prompt.

``RunLoop.plan_prompt`` decides *what* goes into the prompt and in *which
order*; :func:`render_prompt` is the context builder that turns that plan into
the string forwarded to the agent. Keeping the two apart lets a debugging tool
(``nebuko.engine.lib.replay --context``) show which sources fed the prompt
without re-deriving them, and lets tests assert on structure rather than on
substrings of a flattened string.

The plan is pure data (frozen dataclasses, JSON-serializable). Rendering is a
plain join, so ``render_prompt(plan)`` is byte-identical to the legacy
hand-concatenated prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

SECTION_SEPARATOR = "\n\n"


class SectionKind(StrEnum):
    """What a prompt section is, in the order the planner emits them."""

    SCAFFOLD_CONFLICT = "scaffold_conflict"
    ROLE_PROMPT = "role_prompt"
    STEP_PROMPT = "step_prompt"
    OUTPUT_CONTRACT = "output_contract"
    INJECTED_SKILLS = "injected_skills"
    INPUTS = "inputs"
    HANDOFF = "handoff"
    REQUEST_HEADER = "request_header"
    TRIGGER = "trigger"
    TASK = "task"
    ENGINE_STATUS = "engine_status"
    GATE_DECISION = "gate_decision"
    THREAD = "thread"


@dataclass(frozen=True)
class SectionPart:
    """One contributor inside a composite section (e.g. a thread entry)."""

    kind: str
    source: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "source": self.source, "detail": self.detail}


@dataclass(frozen=True)
class PromptSection:
    """One block of the prompt.

    ``content`` is the final text this section contributes (``""`` when it was
    considered but dropped). ``source`` names where it came from (a file path,
    a state key, ``trigger``...). ``reason`` explains an omission or a
    non-obvious inclusion. ``parts`` lists the contributors of a composite
    section.
    """

    kind: SectionKind
    content: str
    source: str = ""
    reason: str = ""
    parts: tuple[SectionPart, ...] = ()

    @property
    def included(self) -> bool:
        return bool(self.content)

    def as_dict(self, *, with_content: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": str(self.kind),
            "source": self.source,
            "included": self.included,
            "chars": len(self.content),
            "reason": self.reason,
            "parts": [part.as_dict() for part in self.parts],
        }
        if with_content:
            data["content"] = self.content
        return data


@dataclass(frozen=True)
class PromptPlan:
    """The ordered sections of one prompt, dropped ones included."""

    sections: tuple[PromptSection, ...] = field(default_factory=tuple)

    def included(self) -> tuple[PromptSection, ...]:
        return tuple(section for section in self.sections if section.included)

    def kinds(self) -> list[str]:
        """Kinds of the sections that reach the prompt, in order."""
        return [str(section.kind) for section in self.included()]

    def get(self, kind: SectionKind) -> PromptSection | None:
        return next((s for s in self.sections if s.kind == kind), None)

    def as_dict(self, *, with_content: bool = True) -> dict[str, Any]:
        return {
            "sections": [
                section.as_dict(with_content=with_content) for section in self.sections
            ]
        }


def render_prompt(plan: PromptPlan) -> str:
    """The context builder: join the included sections in plan order."""
    return SECTION_SEPARATOR.join(section.content for section in plan.included())


def plan_from_sections(sections: Sequence[PromptSection]) -> PromptPlan:
    return PromptPlan(sections=tuple(sections))


def summarize(plan: PromptPlan) -> list[Mapping[str, Any]]:
    """One compact row per section, for tables."""
    return [section.as_dict(with_content=False) for section in plan.sections]
