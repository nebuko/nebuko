"""Tests for the ordered context IR (issue #1346).

``ContextPlan`` is the intermediate representation the engine builds a prompt
from: ordered sections, each with a kind, its text and a provenance source.
``render`` is the single place the prompt text is produced, so the agent still
receives exactly the bytes the string assembly produced before.
"""

from __future__ import annotations

import hashlib

from nebuko.engine.context_ir import ContextPlan, ContextSection, ContextSectionKind


def _section(kind: str, text: str, source: str = "", ids: tuple[str, ...] = ()):
    return ContextSection(kind=kind, text=text, source=source, comment_ids=ids)


class TestContextSectionKind:
    def test_canonical_order_puts_the_scaffold_note_first(self):
        assert ContextSectionKind.ORDER[0] == ContextSectionKind.SCAFFOLD_CONFLICT
        assert ContextSectionKind.ORDER[-1] == ContextSectionKind.RECENT_THREAD
        # Every declared kind appears exactly once in the canonical order.
        declared = {
            value
            for name, value in vars(ContextSectionKind).items()
            if not name.startswith("_") and isinstance(value, str)
        }
        assert set(ContextSectionKind.ORDER) == declared


class TestRender:
    def test_joins_non_empty_sections_with_a_blank_line(self):
        plan = ContextPlan(
            step="plan",
            agent="architect",
            sections=(
                _section(ContextSectionKind.ROLE_PROMPT, "ROLE"),
                _section(ContextSectionKind.STEP_PROMPT, ""),
                _section(ContextSectionKind.TASK, "**Task:** plan"),
            ),
        )

        assert plan.render() == "ROLE\n\n**Task:** plan"

    def test_empty_plan_renders_empty(self):
        assert ContextPlan(step="", agent="", sections=()).render() == ""


class TestPrepend:
    def test_scaffold_note_goes_first(self):
        plan = ContextPlan(
            step="plan",
            agent="architect",
            sections=(_section(ContextSectionKind.ROLE_PROMPT, "ROLE"),),
        )

        prepended = plan.prepended(
            _section(ContextSectionKind.SCAFFOLD_CONFLICT, "CONFLICT")
        )

        assert [section.kind for section in prepended.sections] == [
            ContextSectionKind.SCAFFOLD_CONFLICT,
            ContextSectionKind.ROLE_PROMPT,
        ]
        assert prepended.render().startswith("CONFLICT\n\n")
        # The original plan is untouched (frozen value).
        assert len(plan.sections) == 1


class TestManifest:
    def test_rows_carry_order_provenance_size_and_hash(self):
        plan = ContextPlan(
            step="plan",
            agent="architect",
            sections=(
                _section(ContextSectionKind.ROLE_PROMPT, "ROLE", "role:architect"),
                _section(
                    ContextSectionKind.RECENT_THREAD,
                    "## Recent thread\n\nBODY",
                    "selection:declared",
                    ("42",),
                ),
            ),
        )

        rows = plan.manifest()

        assert [row.order for row in rows] == [0, 1]
        assert [row.kind for row in rows] == [
            ContextSectionKind.ROLE_PROMPT,
            ContextSectionKind.RECENT_THREAD,
        ]
        assert rows[0].source == "role:architect"
        assert rows[1].comment_ids == ["42"]
        assert rows[0].chars == len("ROLE")
        assert rows[0].sha256 == hashlib.sha256(b"ROLE").hexdigest()


class TestRoundTrip:
    def test_to_dict_from_dict_preserves_everything(self):
        plan = ContextPlan(
            step="plan",
            agent="architect",
            sections=(
                _section(ContextSectionKind.ROLE_PROMPT, "ROLE", "role:architect"),
                _section(
                    ContextSectionKind.RECENT_THREAD,
                    "## Recent thread\n\nBODY",
                    "selection:declared",
                    ("42", "43"),
                ),
            ),
            selection_mode="declared",
            missing=("agent-output:context",),
        )

        restored = ContextPlan.from_dict(plan.to_dict())

        assert restored == plan
        assert restored.render() == plan.render()

    def test_from_dict_tolerates_missing_optional_fields(self):
        restored = ContextPlan.from_dict(
            {"step": "plan", "agent": "architect", "sections": [{"kind": "task"}]}
        )

        assert restored.selection_mode == ""
        assert restored.missing == ()
        assert restored.sections[0].text == ""
        assert restored.sections[0].comment_ids == ()
