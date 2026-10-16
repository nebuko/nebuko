"""Terminal emission contract for an agent run (issue #973).

An agent run ends with exactly two artifacts:

1. **the report** — the turn's markdown text, whose section bodies are
   delimited by canonical ``<!-- nebuko:section <name> -->`` pairs. Plain text
   carries no JSON escaping and no termination risk, and a truncated turn still
   leaves every completed section salvageable.
2. **the routing decision** — one small ``finalize(route)`` tool call whose
   ``response_schema`` is the route only.

The engine owns the markdown wrappers (``#`` title, ``##`` heading,
``<details>``) and the marker layer, so
:meth:`OutputRenderer.extract_section` returns exactly what the model wrote —
no rendered wrapper noise leaks into a downstream ``step.field`` input. A
``title: true`` field is the comment's ``#`` lead-in rather than a marker
section, and is read back from that line.

The module is pure: :class:`RunTerminator` takes the posting and routing
side-effects as injected callables, so the gate is unit-testable without a
conversation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, create_model

from nebuko.engine.paths import ensure_import_path

# ``config`` lives on the frozen runtime path (``engine/lib``), which the
# engine makes importable lazily; bootstrap it before the top-level import.
ensure_import_path()

from config.sub_domains.workflow_step.output_field import (  # noqa: E402
    NAME_PATTERN,
    OutputField,
    StepOutput,
)

log = logging.getLogger("nebuko.engine.finalize")

__all__ = [
    "AgentReport",
    "FinalizeSchemaBuilder",
    "MarkerInjectionError",
    "OutputContract",
    "OutputRenderer",
    "RouteDecision",
    "RunTerminator",
    "SECTION_CLOSE",
    "SECTION_OPEN",
    "SectionExtractionError",
]

#: Canonical section markers. The open marker names the field; the close marker
#: is generic. Both are HTML comments, so the marker layer owns ``<!--`` and a
#: field body may never contain it.
SECTION_OPEN = "<!-- nebuko:section {name} -->"
SECTION_CLOSE = "<!-- /nebuko:section -->"

_OPEN_RE = re.compile(r"<!--\s*nebuko:section\s*([^>]*?)\s*-->")
_CLOSE_RE = re.compile(r"<!--\s*/nebuko:section\s*-->")
_NAME_RE = re.compile(NAME_PATTERN)
#: A ``title: true`` field is the comment's ``#`` lead-in, not a marker section.
_TITLE_RE = re.compile(r"^#\s+(.*)$", re.MULTILINE)


class MarkerInjectionError(ValueError):
    """A field body contains ``<!--``, which the marker layer owns."""


class SectionExtractionError(ValueError):
    """A section's markers are missing, mangled, duplicated or hand-edited."""


def _reject_marker_injection(name: str, body: str) -> None:
    """Reject a body that would split the section (fix 2, issue #973)."""
    if "<!--" in body:
        raise MarkerInjectionError(
            f"output field '{name}': the body must not contain '<!--' — the "
            "section marker layer owns HTML comments"
        )


class OutputContract(BaseModel):
    """The declared output of one step: ordered fields plus their rules."""

    fields: list[OutputField] = Field(default_factory=list)

    @classmethod
    def from_step_output(cls, output: Optional[StepOutput]) -> "OutputContract":
        """Build a contract from a step's ``output:`` declaration."""
        return cls(fields=list(output.fields) if output is not None else [])

    def field(self, name: str) -> OutputField:
        """Return the declared field ``name`` or raise ``SectionExtractionError``."""
        for field in self.fields:
            if field.name == name:
                return field
        raise SectionExtractionError(f"unknown output field '{name}'")

    def prompt_block(self) -> str:
        """The per-field section instructions the agent writes against."""
        if not self.fields:
            return ""
        lines = [
            "Write your report as section bodies between these exact markers "
            "(the engine renders the headings and wrappers):",
            "",
        ]
        for field in self.fields:
            if field.title:
                lines.append(f"# …{field.name}…")
                lines.append("")
                continue
            lines.append(SECTION_OPEN.format(name=field.name))
            lines.append(f"…{field.name}…")
            lines.append(SECTION_CLOSE)
            lines.append("")
        lines.append("Fields, in order:")
        for field in self.fields:
            kind = field.type
            if field.type == "table":
                kind += f" (columns: {', '.join(field.columns)})"
            requirement = "required" if field.required else "optional"
            description = f" — {field.description}" if field.description else ""
            lines.append(f"- {field.name} [{kind}, {requirement}]{description}")
        return "\n".join(lines)

    def validate_sections(self, sections: Mapping[str, Optional[str]]) -> None:
        """Validate a harvested answer: known names, required present, no ``<!--``.

        Raises :class:`SectionExtractionError` / :class:`MarkerInjectionError`;
        the tool layer turns the first failure into one retryable tool error
        naming the field, then fails loudly (fix 7, issue #973).
        """
        known = {field.name for field in self.fields}
        for name in sections:
            if name not in known:
                raise SectionExtractionError(f"unknown output field '{name}'")
        for field in self.fields:
            body = sections.get(field.name)
            if body is None:
                if field.required:
                    raise SectionExtractionError(
                        f"required section '{field.name}' is missing"
                    )
                continue
            _reject_marker_injection(field.name, body)


class OutputRenderer:
    """Renders the posted comment and reads section bodies back out."""

    def __init__(self, contract: OutputContract) -> None:
        self.contract = contract

    def validate_body(self, name: str, body: str) -> None:
        """Reject a body that would split the section (shared by parse/render)."""
        _reject_marker_injection(name, body)

    def render(self, sections: Mapping[str, str]) -> str:
        """Render the posted comment from the harvested section bodies.

        The ``#`` title, ``##`` headings and ``<details>`` wrappers are emitted
        here, outside the tight marker pair; absent fields are skipped entirely.
        """
        blocks: list[str] = []
        for field in self.contract.fields:
            body = sections.get(field.name)
            if body is None:
                continue
            self.validate_body(field.name, body)
            if field.title:
                blocks.append(f"# {body.strip()}")
                continue
            body = _canonical_body(field, body)
            block = [f"## {field.rendered_heading}"]
            if field.collapsed:
                block.append(f"<details><summary>{field.rendered_heading}</summary>")
                block.append("")
            block.append(SECTION_OPEN.format(name=field.name))
            block.append(body)
            block.append(SECTION_CLOSE)
            if field.collapsed:
                block.append("</details>")
            blocks.append("\n".join(block))
        return "\n\n".join(blocks)

    def extract_section(
        self, comment: str, name: str, *, allow_unclosed: bool = True
    ) -> Optional[str]:
        """Return the verbatim body of section ``name``.

        ``None`` means "not provided" (only legal for a ``required: false``
        field). A required section whose markers cannot be found raises
        :class:`SectionExtractionError` — extraction never falls back to the
        whole comment (fix 7, issue #973). A ``title: true`` field is read from
        the comment's ``#`` lead-in line instead of a marker pair.
        ``allow_unclosed=False`` restricts the read to sections that carry both
        markers (used by salvage).
        """
        field = self.contract.field(name)
        if field.title:
            return self._extract_title(comment, field)
        opens = list(_OPEN_RE.finditer(comment))
        for match in opens:
            if not _NAME_RE.match(match.group(1)):
                raise SectionExtractionError(
                    f"section marker '{match.group(0)}' is mangled"
                )
        matching = [match for match in opens if match.group(1) == field.name]
        if not matching:
            if field.required:
                raise SectionExtractionError(
                    f"section '{field.name}': markers missing or hand-edited"
                )
            return None
        if len(matching) > 1:
            raise SectionExtractionError(
                f"section '{field.name}': duplicate section markers"
            )
        start = matching[0].end()
        close = _CLOSE_RE.search(comment, start)
        if close is None:
            if not allow_unclosed:
                return None
            next_open = _OPEN_RE.search(comment, start)
            end = next_open.start() if next_open else len(comment)
            log.warning(
                "section '%s': closing marker missing; body bounded by the next "
                "marker or end of comment",
                field.name,
            )
            return comment[start:end].strip("\n")
        return comment[start : close.start()].strip("\n")

    def _extract_title(self, comment: str, field: OutputField) -> Optional[str]:
        """Read a ``title: true`` field from the comment's ``#`` lead-in line."""
        match = _TITLE_RE.search(comment)
        if match is None:
            if field.required:
                raise SectionExtractionError(
                    f"title field '{field.name}': the '# ' lead-in is missing"
                )
            return None
        return match.group(1).strip()


class AgentReport(BaseModel):
    """The harvest-side view of the agent's answer (never tool arguments)."""

    title: str = ""
    sections: dict[str, str] = Field(default_factory=dict)


class RouteDecision(BaseModel):
    """The routing decision carried by the terminal ``finalize`` call."""

    status: Literal["advance", "loop", "hold"] = "advance"
    agent: Optional[str] = None
    complexity: Optional[str] = None
    branch: Optional[str] = None
    reason: Optional[str] = None


class FinalizeSchemaBuilder:
    """Builds the route-only ``finalize`` tool schema (issue #973).

    The report rides in the turn's text, so the tool's top-level arguments are
    exactly ``route`` — a tiny, fixed shape that cannot be truncated by a long
    markdown payload.
    """

    def __init__(self, base: type[RouteDecision] = RouteDecision) -> None:
        self.base = base

    def build(self) -> type[BaseModel]:
        """The finalize action model: one required ``route`` field."""
        return create_model("FinalizeAction", route=(self.base, ...))

    def as_response_schema(self) -> dict[str, Any]:
        """The JSON schema handed to the SDK as ``response_schema``."""
        return self.build().model_json_schema()


class RunTerminator:
    """The terminal gate: extract → validate → render → post → route."""

    def __init__(
        self,
        contract: OutputContract,
        renderer: Optional[OutputRenderer] = None,
        post: Optional[Callable[[str], None]] = None,
        apply_route: Optional[Callable[[RouteDecision], None]] = None,
        default: Optional[RouteDecision] = None,
    ) -> None:
        self.contract = contract
        self.renderer = renderer or OutputRenderer(contract)
        self._post = post or (lambda body: None)
        self._apply_route = apply_route or (lambda route: None)
        self._default = default or RouteDecision(status="hold")

    def finalize(self, route: RouteDecision, turn_text: str) -> AgentReport:
        """Gate the terminal emission and return the harvested report.

        Raises :class:`SectionExtractionError` / :class:`MarkerInjectionError`
        on a malformed answer; the tool layer surfaces the first failure as one
        retryable tool error naming the field, then fails loudly.
        """
        sections = self._extract_all(turn_text)
        self.contract.validate_sections(sections)
        report = AgentReport(
            title=self._title(sections),
            sections={name: body for name, body in sections.items() if body is not None},
        )
        self._post(self.renderer.render(report.sections))
        self._apply_route(route)
        return report

    def salvage(self, turn_text: str) -> AgentReport:
        """Harvest the completed sections of a truncated turn; never raises."""
        sections: dict[str, str] = {}
        for field in self.contract.fields:
            try:
                body = self.renderer.extract_section(
                    turn_text, field.name, allow_unclosed=False
                )
            except SectionExtractionError:
                continue
            if body is not None:
                sections[field.name] = body
        return AgentReport(title=self._title(sections), sections=sections)

    def default_route(self) -> RouteDecision:
        """The route applied when the terminal call is absent or unparseable."""
        return self._default

    def _extract_all(self, turn_text: str) -> dict[str, Optional[str]]:
        return {
            field.name: self.renderer.extract_section(turn_text, field.name)
            for field in self.contract.fields
        }

    def _title(self, sections: Mapping[str, Optional[str]]) -> str:
        for field in self.contract.fields:
            if field.title:
                return (sections.get(field.name) or "").strip()
        return ""


def _canonical_body(field: OutputField, body: str) -> str:
    """Canonicalize a body for rendering (markdown table for ``table``)."""
    if field.type == "table":
        return _canonicalize_table(body)
    return body.strip("\n")


def _canonicalize_table(body: str) -> str:
    """Normalize a markdown table: split on unescaped ``|``, rejoin canonically.

    ``\\|`` inside a cell survives as the markdown-canonical escape; a bare
    ``|`` is a cell separator.
    """
    lines: list[str] = []
    for line in body.strip("\n").splitlines():
        stripped = line.strip()
        if "|" not in stripped:
            lines.append(line)
            continue
        lines.append("| " + " | ".join(_split_row(stripped)) + " |")
    return "\n".join(lines)


def _split_row(row: str) -> list[str]:
    """Split a markdown table row into trimmed cells (``\\|`` stays escaped)."""
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in row:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            current.append(char)
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    if cells and cells[0] == "":
        cells.pop(0)
    if cells and cells[-1] == "":
        cells.pop()
    return cells
