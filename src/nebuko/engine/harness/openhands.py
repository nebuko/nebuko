"""OpenHands harness — the default adapter (issue #1368).

The in-process ``Conversation`` is wrapped in a :class:`HarnessSession`, and the
closing ``finalize`` turn (issues #973, #1033) lives in
:class:`OpenHandsClosingChannel` so the host only delegates to the port.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable, Iterator, Mapping

from nebuko.engine.engine_tools import FinalizeToolError, FinalizeToolHandler
from nebuko.engine.finalize import (
    MarkerInjectionError,
    OutputContract,
    RouteDecision,
    RunTerminator,
    SectionExtractionError,
    turn_outcome_from_action,
)
from nebuko.engine.report_text import (
    REPORT_OFFSET_ATTR,
    closing_turn_report_text as _finalize_turn_text,
    events_of,
    finalize_anchor_index as _finalize_anchor_index,
)

from .base import (
    BuiltAgent,
    ClosingResult,
    HarnessAdapter,
    HarnessCapabilities,
    HarnessEvent,
    drive_turn,
)

__all__ = [
    "OpenHandsClosingChannel",
    "OpenHandsHarness",
    "OpenHandsSession",
    "FINALIZE_BODY_ATTR",
    "FINALIZE_PROMPT",
    "FINALIZE_RETRY_PROMPT",
    "FINALIZE_TOOL_NAMES",
    "finalize_route",
    "finalize_turn_text",
    "salvage_finalize_body",
    "turn_outcome_payload",
]

log = logging.getLogger(__name__)

#: Conversation attribute the closing turn stows its rendered report on
#: (issues #830, #1033), mirroring how ``run_agent`` stows ``run_outcome``.
FINALIZE_BODY_ATTR = "nebuko_finalize_body"

FINALIZE_PROMPT = (
    "Engine finalize turn - the work phase is over; do not call any work tool "
    "(no terminal, read, edit, write, think, ...). Call the `finalize` tool "
    "once. Put "
    "your COMPLETE, self-contained report in its `report` argument - the full "
    "deliverable that must be posted on the issue (never a one-line note) - and "
    "your turn outcome in `turn_outcome`.\n\n"
    "{sections}\n"
    "Set `turn_outcome.appreciation` ('approved' when the "
    "work meets this step's goal, 'rejected' when it does not, 'hold' only "
    "when you are blocked and need information from a human), plus "
    "`turn_outcome.hold_kind` = 'awaiting_input' with a hold, "
    "`turn_outcome.agent_call` = {{agent_name, task}} when you are imperatively "
    "naming the exact agent to run next, and "
    "`turn_outcome.round`/`note`/`complexity`/`pin` when they apply. "
    "Where the workflow goes next is engine topology, never your choice. "
    "Nothing is posted if `report` is empty."
)

FINALIZE_RETRY_PROMPT = (
    "Your previous finalize call carried no report, so nothing could be "
    "posted. Call `finalize` again with the full report in its `report` "
    "argument."
)

#: The closing turn's tool surface (issues #973, #1014).
FINALIZE_TOOL_NAMES = frozenset({"finalize", "finish"})


class OpenHandsSession:
    """A live ``Conversation`` behind the :class:`HarnessSession` protocol."""

    def __init__(self, conversation: Any) -> None:
        self.conversation = conversation
        self._callbacks: list[Callable[[HarnessEvent], None]] = []
        self._seen = 0

    def send_message(self, text: str) -> None:
        self.conversation.send_message(text)

    def run(self, timeout_s: float | None = None) -> None:
        self.conversation.run()
        self._drain()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conversation.interrupt()

    def on_event(self, callback: Callable[[HarnessEvent], None]) -> None:
        self._callbacks.append(callback)

    def _drain(self) -> None:
        """Feed the callbacks the events appended by the turn just run."""
        events = events_of(self.conversation)
        for event in events[self._seen :]:
            self._emit(event)
        self._seen = len(events)

    def _emit(self, event: Any) -> None:
        kind = "action" if getattr(event, "action", None) is not None else "observation"
        name = getattr(event, "tool_name", None) or ""
        for callback in self._callbacks:
            callback(HarnessEvent(kind=kind, text=str(name), data={"event": event}))


class OpenHandsHarness:
    """The default harness: an in-process OpenHands ``Conversation``."""

    name = "openhands"

    def capabilities(self) -> HarnessCapabilities:
        return HarnessCapabilities(
            backend=self.name,
            native_tools=True,
            response_schema=True,
            engine_tools="native",
            mcp="full",
            tool_narrowing=True,
            condenser=True,
            skills_discover=True,
            dispatch_recall=True,
            publication_guard=True,
            closing_channel="tool",
        )

    def build_agent(
        self,
        *,
        role_name: str,
        agent_cfg: Any,
        prompt: str,
        agent_context: Any,
    ) -> BuiltAgent:
        """The OpenHands agent is built by ``run_agent`` itself.

        The legacy assembly owns tool resolution, MCP wiring and the condenser,
        so this adapter only wraps the conversation it produced.
        """
        return BuiltAgent(session=agent_context, prompt=prompt)

    def make_closing_channel(self) -> "OpenHandsClosingChannel":
        return OpenHandsClosingChannel()


class OpenHandsClosingChannel:
    """The in-process ``finalize`` closing turn (issues #973, #1014, #1033)."""

    def elicit(
        self, session: Any, contract: OutputContract | None
    ) -> ClosingResult:
        conversation = getattr(session, "conversation", session)
        if conversation is None:
            return ClosingResult()
        self._elicit_finalize(conversation, contract)
        return ClosingResult(
            report=getattr(conversation, FINALIZE_BODY_ATTR, "") or "",
            outcome=finalize_outcome(conversation),
        )

    def _elicit_finalize(
        self, conversation: Any, contract: OutputContract | None
    ) -> None:
        """Run and harvest the closing ``finalize`` turn (issues #973, #1033)."""
        setattr(conversation, REPORT_OFFSET_ATTR, event_count(conversation))
        prompt = FINALIZE_PROMPT.format(
            sections=contract.prompt_block() if contract is not None else ""
        )
        if contract is None:
            self._elicit_text(conversation, prompt)
            return
        self._elicit_sections(conversation, contract, prompt)

    def _elicit_text(self, conversation: Any, prompt: str) -> None:
        """No declared shape: the turn text *is* the report."""
        text = ""
        for attempt in (1, 2):
            try:
                run_closing_turn(conversation, prompt)
            except Exception as error:  # noqa: BLE001 - fall back, never abort
                log.warning(
                    "[phase:agent] finalize turn failed (%s); keeping the "
                    "captured deliverable",
                    error,
                )
                return
            text = _finalize_turn_text(conversation)
            if text:
                break
            if attempt == 1:
                log.warning(
                    "[phase:agent] finalize turn returned no text; retrying "
                    "once for the report"
                )
                prompt = f"{FINALIZE_RETRY_PROMPT}\n\n{prompt}"
        if text:
            setattr(conversation, FINALIZE_BODY_ATTR, text)
            log.info(
                "[phase:agent] finalize turn text is the report (%d chars)",
                len(text),
            )
        else:
            log.error(
                "[phase:agent] finalize turn returned no text twice; falling "
                "back to the captured deliverable (the work-loop finish note)"
            )

    def _elicit_sections(
        self, conversation: Any, contract: OutputContract, prompt: str
    ) -> None:
        """A declared ``output:`` shape: extract, render, then post."""
        rendered: list[str] = []
        terminator = RunTerminator(
            contract,
            post=rendered.append,
            # The outcome is recorded by the finalize tool's executor into the
            # run-scoped registries, which ``build_output`` reads; the
            # terminator's own hook stays a no-op so it is applied once.
            apply_route=lambda route: None,
        )
        handler = FinalizeToolHandler(terminator)
        for attempt in (1, 2):
            try:
                run_closing_turn(conversation, prompt)
            except Exception as error:  # noqa: BLE001 - degrade to the fallback chain
                log.warning(
                    "[phase:agent] finalize turn failed (%s); salvaging the "
                    "turn text seen so far",
                    error,
                )
                body = salvage_finalize_body(handler, contract, conversation)
                if body:
                    rendered.append(body)
                break
            turn_text = _finalize_turn_text(conversation)
            route = finalize_route(conversation) or RouteDecision(appreciation="hold")
            try:
                handler.handle(route, turn_text)
            # The handler re-raises the raw extraction error on the second
            # failure by design (the tool layer fails loudly); the host is the
            # layer that degrades gracefully, so it catches both (issue #995).
            except (
                FinalizeToolError,
                SectionExtractionError,
                MarkerInjectionError,
            ) as error:
                if attempt == 2:
                    log.error(
                        "[phase:agent] finalize turn failed twice (%s); "
                        "salvaging the completed sections",
                        error,
                    )
                    body = salvage_finalize_body(handler, contract, conversation)
                    if body:
                        rendered.append(body)
                    break
                log.warning(
                    "[phase:agent] finalize turn rejected (%s); retrying once",
                    error,
                )
                prompt = (
                    f"{prompt}\n\nThe previous attempt failed: {error}. Fix the "
                    "section bodies and call `finalize` again."
                )
                continue
            break
        if rendered:
            setattr(conversation, FINALIZE_BODY_ATTR, rendered[-1])
            log.info(
                "[phase:agent] finalize turn rendered the posted comment (%d chars)",
                len(rendered[-1]),
            )
        else:
            log.warning(
                "[phase:agent] finalize turn rendered no comment; falling back "
                "to the captured deliverable (the work-loop finish note)"
            )


def salvage_finalize_body(
    handler: FinalizeToolHandler, contract: OutputContract, conversation: Any
) -> str:
    """Render the completed sections of a broken finalize turn (issue #1014)."""
    from nebuko.engine.report_text import closing_turn_report_candidates

    # A candidate wins only when it yields at least one marker-pair section:
    # a title-only lead-in is exactly the title-only post that starved the next
    # run's prompt (issue #1263), so it is not salvageable.
    try:
        candidates = closing_turn_report_candidates(conversation)
    except Exception:  # noqa: BLE001 - salvage must never raise
        return ""
    for candidate in candidates:
        try:
            report = handler.salvage(candidate)
        except Exception:  # noqa: BLE001 - salvage must never raise
            continue
        if not any(
            not field.title and report.sections.get(field.name)
            for field in contract.fields
        ):
            continue
        try:
            return handler.terminator.renderer.render(report.sections)
        except Exception:  # noqa: BLE001 - salvage must never raise
            continue
    return ""


def turn_outcome_payload(conversation: Any, state: Any) -> dict[str, Any] | None:
    """The run's factual turn-outcome payload (issues #830, #964, #1033)."""
    if getattr(conversation, FINALIZE_BODY_ATTR, None) is None and not has_finalize(
        conversation
    ):
        return None
    return _state_turn_outcome_fields(state) or _recorded_turn_outcome_fields(
        conversation
    )


def finalize_outcome(conversation: Any) -> Any:
    """The typed ``TurnOutcomePayload`` of the closing turn, if it recorded one."""
    from nebuko.engine.lib.agent_script.features.dispatch_next_tool.dispatch_next_tool import (  # noqa: PLC0415
        get_recorded_turn_outcome,
    )

    conv_id = getattr(conversation, "id", None)
    if conv_id is None:
        return None
    recorded = get_recorded_turn_outcome(conv_id)
    if recorded is None:
        return None
    from nebuko.engine.finalize import TurnOutcomePayload  # noqa: PLC0415

    try:
        return TurnOutcomePayload(
            appreciation=recorded.appreciation.value,
            hold_kind=recorded.hold_kind.value if recorded.hold_kind else None,
            note=recorded.note or None,
            round=recorded.round or None,
        )
    except Exception:  # noqa: BLE001 - a malformed record is no outcome
        return None


def _recorded_turn_outcome_fields(conversation: Any) -> dict[str, Any] | None:
    """The turn outcome the finalize executor recorded for this run, as fields."""
    conv_id = getattr(conversation, "id", None)
    if conv_id is None:
        return None
    from nebuko.engine.lib.agent_script.features.dispatch_next_tool.dispatch_next_tool import (  # noqa: PLC0415
        get_recorded_run_prefs,
        get_recorded_turn_outcome,
    )

    outcome = get_recorded_turn_outcome(conv_id)
    if outcome is None:
        return None
    prefs = get_recorded_run_prefs(conv_id)
    fields = _outcome_fields(outcome, prefs) or {}
    handoff = _recorded_agent_call_fields(conv_id)
    if handoff is not None:
        fields["agent_call"] = handoff
    return fields or None


def _recorded_agent_call_fields(conv_id: Any) -> dict[str, Any] | None:
    """The typed hand-off the finalize executor recorded, as payload fields."""
    from nebuko.engine.lib.agent_script.features.call_agent_tool.call_agent_tool import (  # noqa: PLC0415
        get_recorded_function_call,
    )

    handoff = get_recorded_function_call(conv_id)
    if handoff is None:
        return None
    return {"agent_name": handoff.agent_name, "task": handoff.task}


def has_finalize(conversation: Any) -> bool:
    """True when the closing turn recorded a finalize call."""
    return _finalize_anchor_index(conversation) >= 0


def _state_turn_outcome_fields(state: Any) -> dict[str, Any] | None:
    """The workflow state's recorded turn outcome as payload fields."""
    outcome = getattr(state, "turn_outcome", None)
    if outcome is None:
        return None
    return _outcome_fields(outcome, getattr(state, "prefs", None))


def _outcome_fields(outcome: Any, prefs: Any) -> dict[str, Any] | None:
    """The payload-field view of a ``TurnOutcome`` (and its run prefs)."""
    fields: dict[str, Any] = {
        "status": outcome.status.value if outcome.status else "",
        "appreciation": outcome.appreciation.value if outcome.appreciation else "",
        "hold_kind": outcome.hold_kind.value if outcome.hold_kind else "",
        "note": outcome.note or "",
        "complexity": (getattr(prefs, "complexity", "") or "") if prefs else "",
    }
    if outcome.incomplete_reason:
        fields["incomplete_reason"] = outcome.incomplete_reason.value
    return _drop_blanks(fields) or None


def _drop_blanks(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Drop empty payload fields so the outcome carries only stated facts."""
    return {key: value for key, value in fields.items() if value}


@contextlib.contextmanager
def tools_only(conversation: Any, allowed: frozenset[str]) -> Iterator[None]:
    """Restrict a control turn's tool surface to ``allowed`` (issues #830, #973).

    The SDK resolves each step's toolset from the agent's private ``_tools`` map
    (read fresh per LLM call), so the host swaps in the allowed tools for the
    turn's duration and restores the original map afterwards — the same private
    seam the SDK's own runtime tool registration uses. Hosts that do not expose
    that map (or that would not be narrowed) are left untouched; the turn prompt
    still constrains the turn.
    """
    agent = getattr(conversation, "agent", None)
    resolved = getattr(agent, "_tools", None)
    if not isinstance(resolved, dict) or not resolved:
        yield
        return
    narrowed = {name: tool for name, tool in resolved.items() if name in allowed}
    if not narrowed or len(narrowed) == len(resolved):
        yield
        return

    def _install(tools: dict) -> None:
        lock = getattr(agent, "_tools_lock", None)
        if lock is None:
            object.__setattr__(agent, "_tools", tools)
            return
        with lock:
            object.__setattr__(agent, "_tools", tools)

    _install(narrowed)
    log.info("[phase:agent] control turn tool surface restricted to %s", sorted(narrowed))
    try:
        yield
    finally:
        _install(resolved)


def finalize_route(conversation: Any) -> RouteDecision | None:
    """The typed route of the finalize turn's last ``finalize`` call (#1033)."""
    anchor = _finalize_anchor_index(conversation)
    if anchor < 0:
        return None
    outcome = turn_outcome_from_action(
        getattr(events_of(conversation)[anchor], "action", None)
    )
    if outcome is None:
        return None
    return RouteDecision(
        appreciation=outcome.appreciation,
        hold_kind=outcome.hold_kind,
        round=outcome.round,
        note=outcome.note,
        complexity=outcome.complexity,
        pin=outcome.pin,
    )


def run_closing_turn(conversation: Any, prompt: str) -> None:
    """Run one closing turn with the tool surface narrowed to the terminal tool."""
    with tools_only(conversation, FINALIZE_TOOL_NAMES):
        drive_turn(OpenHandsSession(conversation), prompt)


def event_count(conversation: Any) -> int:
    return len(events_of(conversation))


def finalize_turn_text(conversation: Any) -> str:
    """The closing turn's report text (re-exported for the host)."""
    return _finalize_turn_text(conversation)
