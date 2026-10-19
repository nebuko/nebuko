"""Terminal emission contract for an agent run (issue #973).

An agent run ends with exactly two artifacts:

1. **the report** — the turn's markdown text, whose section bodies are
   delimited by canonical ``<!-- nebuko:section <name> -->`` pairs. Plain text
   carries no JSON escaping and no termination risk, and a truncated turn still
   leaves every completed section salvageable.
2. **the routing decision** — one small ``finalize(turn_outcome)`` tool call
   whose ``response_schema`` is the turn outcome only (issue #1033). Every
   step ends this way; an ``output:`` declaration only decides how the report
   is extracted and rendered.

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

from pydantic import BaseModel, ConfigDict, Field, create_model

from nebuko.engine.paths import ensure_import_path

# ``config`` lives on the frozen runtime path (``engine/lib``), which the
# engine makes importable lazily; bootstrap it before the top-level import.
ensure_import_path()

from config.sub_domains.workflow_step.output_field import (  # noqa: E402
    NAME_PATTERN,
    OutputField,
    StepOutput,
)
from workflow_state import (  # noqa: E402 - runtime-path module
    Appreciation,
    FunctionCall,
    HoldKind,
    TurnOutcome,
    TurnStatus,
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
    "StrictFinalizePayload",
    "TurnOutcomePayload",
    "route_from_action",
    "step_output_contract",
    "turn_outcome_from_action",
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


def _reject_placeholder(name: str, body: str) -> None:
    # @stub-protected: signature defined by architect — do not modify
    """Reject a body that is still the prompt's placeholder (issue #1020).

    The engine's own prompt block (``OutputContract.prompt_block``) shows
    ``…{name}…`` as the body to replace. When that prompt is harvested as the
    turn text — the model answered with no text, or an engine prompt event was
    mistaken for the agent's turn — extraction finds the skeleton, every field
    validates and the posted comment is the template itself. Locate the
    placeholder, never echo it back.
    """
    if body.strip() == f"…{name}…":
        raise SectionExtractionError(
            f"section '{name}': body is still the template placeholder"
        )


def step_output_contract(raw: Any) -> Optional["OutputContract"]:
    """Compile the resolver's ``STEP_OUTPUT`` JSON into an :class:`OutputContract`.

    ``None`` means the step declares no output shape, so the run keeps the
    legacy ``finish`` / ``dispatch_next`` terminal contract (issue #973). A
    malformed declaration is a configuration error: it is logged and treated as
    undeclared rather than crashing the run.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        output = StepOutput.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - a bad declaration must not crash the run
        log.warning("STEP_OUTPUT is not a valid step-output declaration: %s", exc)
        return None
    return OutputContract.from_step_output(output)


def route_from_action(action: object) -> Optional["RouteDecision"]:
    """Read the typed route out of a ``finalize`` action's structured output.

    The route arrives through the tool's ``response_schema`` (issue #973), so it
    lands in ``action.structured_output`` rather than in a declared action field.
    Returns ``None`` when the action carries no parseable route, so a malformed
    call surfaces to the agent instead of poisoning downstream routing.
    """
    raw = getattr(action, "structured_output", None)
    if not isinstance(raw, dict):
        return None
    route = raw.get("route")
    if route is None:
        return None
    try:
        return RouteDecision.model_validate(route)
    except Exception as exc:  # noqa: BLE001 - a bad route must not crash the run
        log.warning("finalize route failed RouteDecision validation: %s", exc)
        return None


def turn_outcome_from_action(action: object) -> Optional["TurnOutcomePayload"]:
    """Read the agent-supplied ``turn_outcome`` out of a ``finalize`` action.

    The action declares a single ``turn_outcome`` field (the report rides in
    the turn text). A malformed payload returns ``None`` so the tool executor
    can report it back to the agent instead of poisoning the run's routing.
    """
    raw = getattr(action, "structured_output", None)
    if not isinstance(raw, dict) or "turn_outcome" not in raw:
        raw = getattr(action, "turn_outcome", None)
        if isinstance(raw, BaseModel):
            return TurnOutcomePayload.model_validate(raw.model_dump())
        if not isinstance(raw, dict):
            return None
        value = raw
    else:
        value = raw["turn_outcome"]
    try:
        return TurnOutcomePayload.model_validate(value)
    except Exception as exc:  # noqa: BLE001 - a bad verdict must not crash the run
        log.warning("finalize turn_outcome failed validation: %s", exc)
        return None


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
            body = comment[start:end].strip("\n")
        else:
            body = comment[start : close.start()].strip("\n")
        _reject_placeholder(field.name, body)
        return body

    def _extract_title(self, comment: str, field: OutputField) -> Optional[str]:
        """Read a ``title: true`` field from the comment's ``#`` lead-in line."""
        match = _TITLE_RE.search(comment)
        if match is None:
            if field.required:
                raise SectionExtractionError(
                    f"title field '{field.name}': the '# ' lead-in is missing"
                )
            return None
        title = match.group(1).strip()
        _reject_placeholder(field.name, title)
        return title


class AgentReport(BaseModel):
    """The harvest-side view of the agent's answer (never tool arguments)."""

    title: str = ""
    sections: dict[str, str] = Field(default_factory=dict)


class RouteDecision(BaseModel):
    """The routing decision carried by the terminal ``finalize`` call.

    The factual turn-outcome vocabulary (issue #964): the agent reports what
    happened and the engine derives the route from the workflow topology, so
    there is no navigation intent (``advance``/``loop``) here.

    Worker-side (lenient) reading of ``route`` — the agent-facing schema is
    :class:`TurnOutcomePayload` (issue #1033); this model only tolerates what a
    not-yet-migrated worker still emits.
    """

    appreciation: Literal["approved", "rejected", "hold"] = "hold"
    hold_kind: Optional[Literal["awaiting_approval", "awaiting_input"]] = None
    required_agent: Optional[str] = None
    round: Optional[int] = None
    note: Optional[str] = None
    complexity: Optional[str] = None
    #: Payload parity with ``dispatch_next``: a ``finalize`` route must be able
    #: to pin the step's comment for verbatim downstream injection.
    pin: bool = False


class AgentCall(BaseModel):
    """The imperative agent-to-agent hand-off carried by ``finalize``.

    This is the only way an agent names an exact hand-off target (issue
    #1033): ``dispatch_next``'s ``required_agent`` and the standalone
    ``call_agent`` tool are gone. The return address is engine-derived, so a
    ``callback`` can never be set — or read — by an agent.
    """

    model_config = ConfigDict(extra="forbid")

    agent_name: str
    task: Optional[str] = None


#: The single valid hold kind: a hold asks a *human* for input. An approval
#: gate is engine-owned run state, never an agent verdict.
AWAITING_INPUT = "awaiting_input"

_COMPLEXITY_TIERS = ("low", "medium", "high")


class TurnOutcomePayload(BaseModel):
    """The typed ``turn_outcome`` argument of the terminal ``finalize`` call.

    Fields stay optional in the schema; the conditional rules are enforced at
    runtime by :meth:`validate_runtime`, whose message is returned to the agent
    as a tool error (the plan's runtime-validation decision, issue #1033).

    The engine-measured axes (``status``, ``incomplete_reason``) and the
    removed channels (``required_agent``, ``awaiting_approval``) are rejected
    by ``extra="forbid"``: the agent can supply neither.
    """

    model_config = ConfigDict(extra="forbid")

    appreciation: Literal["approved", "rejected", "hold"]
    hold_kind: Optional[Literal["awaiting_input"]] = None
    note: Optional[str] = None
    round: Optional[int] = None
    complexity: Optional[str] = None
    pin: bool = False
    agent_call: Optional[AgentCall] = None

    def validate_runtime(self) -> None:
        """Enforce the conditional rules; raise ``ValueError`` for the agent.

        Raises:
            ValueError: With an agent-facing message naming the offending field
                (``hold_kind`` iff ``hold``, a known complexity tier, a
                non-negative ``round``, a non-blank ``agent_call.agent_name``).
        """
        if self.appreciation == "hold" and self.hold_kind is None:
            raise ValueError(
                "hold_kind is required when appreciation='hold': use "
                f"'{AWAITING_INPUT}' (an approval gate is engine-owned and is "
                "never requested by the agent)"
            )
        if self.appreciation != "hold" and self.hold_kind is not None:
            raise ValueError(
                f"hold_kind is only valid when appreciation='hold' (got "
                f"appreciation='{self.appreciation}')"
            )
        if self.complexity is not None and self.complexity not in _COMPLEXITY_TIERS:
            raise ValueError(
                f"invalid complexity {self.complexity!r}: expected one of "
                f"{list(_COMPLEXITY_TIERS)}"
            )
        if self.round is not None and self.round < 0:
            raise ValueError(f"round must be >= 0 (got {self.round})")
        if self.agent_call is not None and not self.agent_call.agent_name.strip():
            raise ValueError("agent_call.agent_name is required and must not be blank")

    def to_turn_outcome(self, *, status: TurnStatus = TurnStatus.COMPLETED) -> TurnOutcome:
        """Map onto the factual ``TurnOutcome`` model.

        ``status`` is engine-measured and never agent-supplied; the terminal
        call only exists on a completed turn, so it defaults to COMPLETED.
        """
        return TurnOutcome(
            status=status,
            appreciation=Appreciation(self.appreciation),
            hold_kind=HoldKind(self.hold_kind) if self.hold_kind else None,
            round=self.round or 0,
            note=self.note or "",
        )

    def to_function_call(self) -> Optional[FunctionCall]:
        """The typed hand-off this outcome requests, or ``None``.

        A hold never hands off: the callee is recalled, not delegated to.
        """
        if self.agent_call is None or self.appreciation == "hold":
            return None
        return FunctionCall(
            agent_name=self.agent_call.agent_name.strip(),
            task=self.agent_call.task,
            complexity=self.complexity or "",
        )


class StrictFinalizePayload(TurnOutcomePayload):
    """The SDK ``response_schema`` payload of the ``finalize`` tool.

    Same fields and runtime rules as :class:`TurnOutcomePayload`, with
    ``appreciation`` promoted to required in the *schema* so a missing verdict
    is rejected before the executor runs.
    """

    appreciation: Literal["approved", "rejected", "hold"] = Field(
        ..., description="Your verdict on this turn's work."
    )


class FinalizeSchemaBuilder:
    """Builds the ``turn_outcome``-only ``finalize`` tool schema (issue #1033).

    This is the ``response_schema`` half of the tool: exactly ``turn_outcome``,
    a tiny, fixed shape. The report is the other half, the ``report`` field
    declared on ``FinalizeAction`` (issue #1038), so the SDK merges the two.
    """

    def __init__(self, base: type[TurnOutcomePayload] = StrictFinalizePayload) -> None:
        self.base = base

    def build(self) -> type[BaseModel]:
        """The finalize action model: one required ``turn_outcome`` field."""
        return create_model("FinalizeAction", turn_outcome=(self.base, ...))

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
        self._default = default or RouteDecision(appreciation="hold")

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
