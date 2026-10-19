"""Tests for the terminal emission contract (issue #973)."""

from __future__ import annotations

import pytest

from nebuko.engine.finalize import (
    AgentReport,
    FinalizeSchemaBuilder,
    MarkerInjectionError,
    OutputContract,
    OutputRenderer,
    RouteDecision,
    RunTerminator,
    SectionExtractionError,
)
from config import OutputField


def _contract() -> OutputContract:
    return OutputContract(
        fields=[
            OutputField(name="headline", title=True, description="one-line outcome"),
            OutputField(name="implementation", collapsed=True),
            OutputField(name="questions", required=False),
        ]
    )


def _wire(**bodies: str) -> str:
    parts = []
    for name, body in bodies.items():
        if name == "headline":  # the title field is the ``#`` lead-in
            parts.append(f"# {body}")
        else:
            parts.append(
                f"<!-- nebuko:section {name} -->\n{body}\n<!-- /nebuko:section -->"
            )
    return "\n\n".join(parts)


class TestRender:
    def test_wrappers_stay_outside_the_markers(self):
        rendered = OutputRenderer(_contract()).render(
            {"headline": "Outcome", "implementation": "1. do it"}
        )
        assert rendered.startswith("# Outcome")
        assert "## Implementation" in rendered
        assert "<details><summary>Implementation</summary>" in rendered
        assert "</details>" in rendered
        # The body sits verbatim between the tight marker pair.
        assert (
            "<!-- nebuko:section implementation -->\n1. do it\n"
            "<!-- /nebuko:section -->"
        ) in rendered

    def test_absent_optional_field_is_skipped_entirely(self):
        rendered = OutputRenderer(_contract()).render({"implementation": "1. do it"})
        assert "questions" not in rendered
        assert "## Questions" not in rendered

    def test_title_field_is_the_lead_in_without_markers(self):
        rendered = OutputRenderer(_contract()).render({"headline": "Outcome"})
        assert rendered == "# Outcome"

    def test_table_body_is_canonicalized(self):
        contract = OutputContract(
            fields=[OutputField(name="rows", type="table", columns=["a", "b"])]
        )
        rendered = OutputRenderer(contract).render({"rows": "|a|b|\n|---|---|"})
        assert "| a | b |" in rendered
        assert "| --- | --- |" in rendered

    def test_escaped_pipe_survives_canonicalization(self):
        contract = OutputContract(
            fields=[OutputField(name="rows", type="table", columns=["a", "b"])]
        )
        rendered = OutputRenderer(contract).render({"rows": r"| a\|b | c |"})
        assert r"| a\|b | c |" in rendered


class TestRoundTrip:
    def test_extract_returns_the_verbatim_body(self):
        renderer = OutputRenderer(_contract())
        for body in ("plain text", "line1\nline2", "```\ncode\n```", "a --> b"):
            rendered = renderer.render({"implementation": body})
            assert renderer.extract_section(rendered, "implementation") == body

    def test_render_extract_is_idempotent_at_body_level(self):
        renderer = OutputRenderer(_contract())
        rendered = renderer.render(
            {"headline": "Outcome", "implementation": "1. do it"}
        )
        rebuilt = renderer.render(
            {
                name: renderer.extract_section(rendered, name)
                for name in ("headline", "implementation")
            }
        )
        assert rebuilt == rendered

    def test_title_field_round_trips_through_the_lead_in(self):
        renderer = OutputRenderer(_contract())
        rendered = renderer.render({"headline": "Outcome"})
        assert renderer.extract_section(rendered, "headline") == "Outcome"

    def test_table_round_trip_is_canonical(self):
        contract = OutputContract(
            fields=[OutputField(name="rows", type="table", columns=["a", "b"])]
        )
        renderer = OutputRenderer(contract)
        canonical = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        rendered = renderer.render({"rows": canonical})
        assert renderer.extract_section(rendered, "rows") == canonical


class TestMarkerInjection:
    def test_body_with_open_marker_is_rejected(self):
        renderer = OutputRenderer(_contract())
        with pytest.raises(MarkerInjectionError, match="implementation"):
            renderer.validate_body("implementation", "<!-- nebuko:section x -->")

    def test_body_with_close_marker_is_rejected(self):
        renderer = OutputRenderer(_contract())
        with pytest.raises(MarkerInjectionError):
            renderer.render({"implementation": "text <!-- /nebuko:section --> more"})

    def test_validate_sections_rejects_injection(self):
        contract = _contract()
        with pytest.raises(MarkerInjectionError):
            contract.validate_sections(
                {"headline": "x", "implementation": "<!-- /nebuko:section -->"}
            )

    def test_plain_html_and_code_blocks_pass(self):
        renderer = OutputRenderer(_contract())
        for body in ("a --> b", "<details>ok</details>", "```\ncode\n```"):
            renderer.validate_body("implementation", body)


class TestExtractionAnomalies:
    def test_missing_required_section_raises(self):
        renderer = OutputRenderer(_contract())
        with pytest.raises(SectionExtractionError, match="markers missing"):
            renderer.extract_section(_wire(headline="x"), "implementation")

    def test_missing_optional_section_is_not_provided(self):
        renderer = OutputRenderer(_contract())
        assert renderer.extract_section(_wire(headline="x"), "questions") is None

    def test_provided_but_empty_is_empty_string(self):
        renderer = OutputRenderer(_contract())
        wire = "<!-- nebuko:section questions -->\n<!-- /nebuko:section -->"
        assert renderer.extract_section(wire, "questions") == ""

    def test_duplicate_section_raises(self):
        renderer = OutputRenderer(_contract())
        wire = _wire(implementation="a") + "\n\n" + _wire(implementation="b")
        with pytest.raises(SectionExtractionError, match="duplicate"):
            renderer.extract_section(wire, "implementation")

    def test_mangled_marker_raises(self):
        renderer = OutputRenderer(_contract())
        wire = "<!-- nebuko:section -->\nbody\n<!-- /nebuko:section -->"
        with pytest.raises(SectionExtractionError, match="mangled"):
            renderer.extract_section(wire, "implementation")

    def test_unknown_field_raises(self):
        renderer = OutputRenderer(_contract())
        with pytest.raises(SectionExtractionError, match="unknown"):
            renderer.extract_section(_wire(headline="x"), "nope")

    def test_missing_close_is_a_defensive_branch(self):
        renderer = OutputRenderer(_contract())
        wire = "<!-- nebuko:section implementation -->\n1. do it"
        assert renderer.extract_section(wire, "implementation") == "1. do it"

    def test_missing_close_is_bounded_by_the_next_marker(self):
        renderer = OutputRenderer(_contract())
        wire = (
            "<!-- nebuko:section implementation -->\n1. do it\n"
            "<!-- nebuko:section questions -->\nq"
        )
        assert renderer.extract_section(wire, "implementation") == "1. do it"

    def test_complete_only_read_skips_an_unclosed_section(self):
        renderer = OutputRenderer(_contract())
        wire = "<!-- nebuko:section implementation -->\n1. do it"
        assert (
            renderer.extract_section(wire, "implementation", allow_unclosed=False)
            is None
        )


class TestPlaceholderRejection:
    """Issue #1020: the prompt's own skeleton must never validate as a report."""

    def test_section_body_that_is_the_placeholder_raises(self):
        renderer = OutputRenderer(_contract())
        with pytest.raises(SectionExtractionError, match="template placeholder"):
            renderer.extract_section(_wire(implementation="…implementation…"), "implementation")

    def test_title_that_is_the_placeholder_raises(self):
        renderer = OutputRenderer(_contract())
        with pytest.raises(SectionExtractionError, match="template placeholder"):
            renderer.extract_section(_wire(headline="…headline…"), "headline")

    def test_the_placeholder_is_rejected_even_when_unclosed(self):
        renderer = OutputRenderer(_contract())
        wire = "<!-- nebuko:section implementation -->\n…implementation…"
        with pytest.raises(SectionExtractionError, match="template placeholder"):
            renderer.extract_section(wire, "implementation")

    def test_salvage_skips_a_placeholder_section(self):
        """A placeholder body is not salvageable; the fallback chain runs instead."""
        report = RunTerminator(_contract()).salvage(
            _wire(headline="Real outcome", implementation="…implementation…")
        )
        assert set(report.sections) == {"headline"}
        assert report.title == "Real outcome"

    def test_real_content_still_passes(self):
        renderer = OutputRenderer(_contract())
        wire = _wire(headline="Outcome", implementation="real body")
        assert renderer.extract_section(wire, "implementation") == "real body"
        assert renderer.extract_section(wire, "headline") == "Outcome"

    def test_an_empty_optional_section_is_not_a_placeholder(self):
        renderer = OutputRenderer(_contract())
        wire = "<!-- nebuko:section questions -->\n<!-- /nebuko:section -->"
        assert renderer.extract_section(wire, "questions") == ""

    def test_a_body_merely_mentioning_the_placeholder_passes(self):
        renderer = OutputRenderer(_contract())
        wire = _wire(implementation="replace the `…implementation…` line")
        assert (
            renderer.extract_section(wire, "implementation")
            == "replace the `…implementation…` line"
        )

    def test_the_rendered_prompt_block_never_renders_a_comment(self):
        """The reproduced defect: echoing the engine's prompt block posts the template."""
        contract = _contract()
        with pytest.raises(SectionExtractionError):
            RunTerminator(contract).finalize(
                RouteDecision(appreciation="approved"), contract.prompt_block()
            )


class TestPromptBlock:
    def test_prompt_block_lists_fields_and_markers(self):
        block = _contract().prompt_block()
        assert "<!-- nebuko:section implementation -->" in block
        assert "- implementation [text, required]" in block
        assert "- questions [text, optional]" in block

    def test_empty_contract_has_no_prompt_block(self):
        assert OutputContract().prompt_block() == ""


class TestFinalizeSchemaBuilder:
    def test_schema_top_level_is_route_only(self):
        schema = FinalizeSchemaBuilder().as_response_schema()
        assert set(schema["properties"]) == {"route"}
        assert schema["required"] == ["route"]

    def test_route_content_rides_via_defs(self):
        schema = FinalizeSchemaBuilder().as_response_schema()
        assert schema["properties"]["route"] == {"$ref": "#/$defs/RouteDecision"}
        assert "RouteDecision" in schema["$defs"]

    def test_build_accepts_a_route(self):
        action = FinalizeSchemaBuilder().build()(route={"appreciation": "approved"})
        assert action.route.appreciation == "approved"


class TestRunTerminator:
    def _terminator(self, posted: list[str], routed: list[RouteDecision]):
        return RunTerminator(
            _contract(),
            post=posted.append,
            apply_route=routed.append,
        )

    def test_finalize_posts_renders_and_routes(self):
        posted: list[str] = []
        routed: list[RouteDecision] = []
        terminator = self._terminator(posted, routed)
        route = RouteDecision(appreciation="approved", complexity="low")
        report = terminator.finalize(
            route, _wire(headline="Outcome", implementation="1. do it")
        )
        assert isinstance(report, AgentReport)
        assert report.title == "Outcome"
        assert report.sections == {"headline": "Outcome", "implementation": "1. do it"}
        assert posted and posted[0].startswith("# Outcome")
        assert routed == [route]

    def test_finalize_rejects_a_missing_required_section(self):
        posted: list[str] = []
        routed: list[RouteDecision] = []
        terminator = self._terminator(posted, routed)
        with pytest.raises(SectionExtractionError):
            terminator.finalize(RouteDecision(), _wire(headline="Outcome"))
        assert posted == [] and routed == []

    def test_salvage_keeps_only_completed_sections(self):
        terminator = self._terminator([], [])
        wire = (
            "# Outcome\n\n"
            "<!-- nebuko:section implementation -->\n1. truncated"
        )
        report = terminator.salvage(wire)
        assert report.sections == {"headline": "Outcome"}
        assert report.title == "Outcome"

    def test_default_route_is_hold(self):
        assert self._terminator([], []).default_route().appreciation == "hold"
