"""The prompt plan IR: ordered sections, joined by the context builder."""

from nebuko.engine.prompt_plan import (
    PromptPlan,
    PromptSection,
    SectionKind,
    SectionPart,
    render_prompt,
)


def _plan() -> PromptPlan:
    return PromptPlan(
        (
            PromptSection(SectionKind.ROLE_PROMPT, "", source="r.md", reason="dropped"),
            PromptSection(SectionKind.STEP_PROMPT, "STEP", source="s.md"),
            PromptSection(
                SectionKind.THREAD,
                "THREAD",
                parts=(SectionPart("agent-output", "42", "agent output — plan"),),
            ),
        )
    )


def test_render_joins_included_sections_in_order():
    assert render_prompt(_plan()) == "STEP\n\nTHREAD"


def test_dropped_sections_stay_in_the_plan_with_their_reason():
    plan = _plan()
    role = plan.get(SectionKind.ROLE_PROMPT)
    assert role is not None and not role.included and role.reason == "dropped"
    assert plan.kinds() == ["step_prompt", "thread"]


def test_plan_is_json_serializable_and_can_hide_content():
    data = _plan().as_dict(with_content=False)
    assert [s["kind"] for s in data["sections"]] == [
        "role_prompt",
        "step_prompt",
        "thread",
    ]
    assert "content" not in data["sections"][1]
    assert data["sections"][2]["parts"][0]["source"] == "42"
    assert data["sections"][2]["chars"] == len("THREAD")
