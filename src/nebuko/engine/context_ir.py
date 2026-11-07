"""Ordered intermediate representation of an agent's input context (issue #1346).

``build_prompt`` used to join strings straight into the prompt, so the order of
the pieces and where each one came from were lost as soon as the text was
assembled. :class:`ContextPlan` keeps that structure: an ordered tuple of
:class:`ContextSection` values, each with a kind, its rendered text and a
provenance ``source``. :meth:`ContextPlan.render` is the single place the
prompt text is produced, so the agent still receives exactly the same bytes.

The plan is pure data: no engine imports beyond typing, so it can be recorded
(to the run artifacts and, compactly, to workflow state) and read back offline
by the replay helper.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Mapping

__all__ = [
    "ContextPlan",
    "ContextSection",
    "ContextSectionKind",
]


class ContextSectionKind:
    """Kinds of section a plan can carry, in their canonical order."""

    SCAFFOLD_CONFLICT = "scaffold_conflict"
    ROLE_PROMPT = "role_prompt"
    STEP_PROMPT = "step_prompt"
    STEP_OUTPUT = "step_output"
    INJECTED_SKILLS = "injected_skills"
    STEP_INPUTS = "step_inputs"
    PREVIOUS_HANDOFF = "previous_handoff"
    USER_REQUEST_HEADER = "user_request_header"
    REQUEST_AUTHOR = "request_author"
    ISSUE_BODY = "issue_body"
    REQUEST_BODY = "request_body"
    TASK = "task"
    ENGINE_STATUS = "engine_status"
    GATE_DECISION = "gate_decision"
    RECENT_THREAD = "recent_thread"

    #: Canonical order of the sections a plan emits (scaffold note first).
    ORDER: tuple[str, ...] = (
        SCAFFOLD_CONFLICT,
        ROLE_PROMPT,
        STEP_PROMPT,
        STEP_OUTPUT,
        INJECTED_SKILLS,
        STEP_INPUTS,
        PREVIOUS_HANDOFF,
        USER_REQUEST_HEADER,
        REQUEST_AUTHOR,
        ISSUE_BODY,
        REQUEST_BODY,
        TASK,
        ENGINE_STATUS,
        GATE_DECISION,
        RECENT_THREAD,
    )


@dataclass(frozen=True)
class ContextSection:
    """One ordered piece of the prompt, with the provenance of its text."""

    kind: str
    text: str
    #: Where the text came from, e.g. ``role:dev``, ``step:plan``,
    #: ``input:diff``, ``skill:alpha``, ``trigger:body``.
    source: str = ""
    #: Platform comment ids the text was built from (thread/request sections).
    comment_ids: tuple[str, ...] = ()

    def to_manifest_row(self, order: int) -> "ContextManifestRow":
        """The body-free, order-preserving row recorded in workflow state."""
        from nebuko.engine.lib.workflow_state.sub_domains.context_manifest_row import (
            ContextManifestRow,
        )

        return ContextManifestRow(
            order=order,
            kind=self.kind,
            source=self.source,
            comment_ids=list(self.comment_ids),
            chars=len(self.text),
            sha256=hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "source": self.source,
            "comment_ids": list(self.comment_ids),
        }


@dataclass(frozen=True)
class ContextPlan:
    """The ordered sections that make up one agent prompt."""

    step: str
    agent: str
    sections: tuple[ContextSection, ...]
    #: Which selection rule produced the thread section (``""`` when unknown).
    selection_mode: str = ""
    #: ``"kind:step"`` of every declared input selection could not find.
    missing: tuple[str, ...] = ()

    def render(self) -> str:
        """The prompt text: non-empty section texts joined by a blank line."""
        return "\n\n".join(section.text for section in self.sections if section.text)

    def prepended(self, section: ContextSection) -> "ContextPlan":
        """A copy of this plan with ``section`` placed first."""
        return replace(self, sections=(section, *self.sections))

    def manifest(self) -> list["ContextManifestRow"]:
        """The compact, body-free rows persisted on the history entry."""
        return [
            section.to_manifest_row(order)
            for order, section in enumerate(self.sections)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "agent": self.agent,
            "selection_mode": self.selection_mode,
            "missing": list(self.missing),
            "sections": [section.to_dict() for section in self.sections],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextPlan":
        return cls(
            step=str(data.get("step") or ""),
            agent=str(data.get("agent") or ""),
            sections=tuple(
                ContextSection(
                    kind=str(item.get("kind") or ""),
                    text=str(item.get("text") or ""),
                    source=str(item.get("source") or ""),
                    comment_ids=tuple(
                        str(cid) for cid in (item.get("comment_ids") or ())
                    ),
                )
                for item in (data.get("sections") or ())
                if isinstance(item, Mapping)
            ),
            selection_mode=str(data.get("selection_mode") or ""),
            missing=tuple(str(item) for item in (data.get("missing") or ())),
        )

