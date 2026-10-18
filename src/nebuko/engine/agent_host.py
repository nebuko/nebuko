"""In-process agent host for the engine (issue #740; #590 Stage A).

The engine hosts the legacy ``engine/lib/agent_script`` assembly *in-process*
so it can observe the conversation's ordered turn stream and freeze the agent's
real deliverable at the first end-tool boundary
(:class:`~nebuko.engine.answer_capture.AnswerCapture` — Option B of issue #740). The
frozen ``engine/lib`` package is only imported, never edited, so the capture
stays engine-only.

:func:`nebuko.engine.agent_runner.run_legacy_agent` keeps the subprocess boundary as the
fallback for a host where this assembly cannot start; both seams reuse the same
legacy assembly and normalize its artefacts into the mapping the driver's
``validate``/``publish``/``route`` phases consume.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping

from nebuko.engine.agent_runner import (
    EngineAgentUnavailable,
    _AGENT_SCRIPT,
    _CONSOLE_LOG_FILE,
    _FINAL_MESSAGE_FILE,
    _LATEST_ANSWER_FILE,
    _STATE_FILE,
    _child_env,
    _load_state,
    _read_text,
)
from nebuko.engine.answer_capture import AnswerCapture
from nebuko.engine.engine_tools import (
    DEFAULT_ENGINE_TOOLS,
    EngineToolPayload,
    FinalizeToolError,
    FinalizeToolHandler,
)
from nebuko.engine.finalize import (
    MarkerInjectionError,
    OutputContract,
    OutputRenderer,
    RouteDecision,
    RunTerminator,
    SectionExtractionError,
    route_from_action,
    step_output_contract,
)
from nebuko.engine.paths import ensure_import_path

__all__ = ["InProcessAgentHost"]

log = logging.getLogger(__name__)

_MISSING = object()

#: Conversation attributes the report turn stows its results on (issue #830),
#: mirroring how ``run_agent`` stows ``run_outcome`` for ``build_output``.
_REPORT_PAYLOAD_ATTR = "nebuko_report_payload"
_REPORT_ROUTING_ATTR = "nebuko_report_routing"
_REPORT_FUNCTION_CALL_ATTR = "nebuko_report_function_call"
_REPORT_OFFSET_ATTR = "nebuko_report_event_offset"
#: The rendered comment of a step that declares an ``output:`` shape (issue
#: #973). The finalize turn replaces the report turn for such a step, so the
#: deliverable is the engine-rendered comment, not a ``finish.message``.
_FINALIZE_BODY_ATTR = "nebuko_finalize_body"

#: Engine-elicited report turn (issue #830). After the work loop ends the
#: engine takes the hand and asks for the deliverable in the control plane's
#: typed slots, so the posted answer never depends on where the agent placed
#: its prose (the #822 root cause). One turn carries both control calls.
_REPORT_PROMPT = (
    "Engine report turn - the work phase is over; do not call any work tool "
    "(no terminal, file_editor, think, ...). Reply by calling the control-plane "
    "tools only:\n"
    "1. `finish` with `message` set to your COMPLETE, self-contained "
    "deliverable for this run - the full report that must be posted on the "
    "GitHub issue (not a one-line status note).\n"
    "2. `dispatch_next` with your factual verdict on this turn (the required "
    "`appreciation` of approved/rejected/hold, plus `hold_kind` with a hold, "
    "and `required_agent`/`round`/`note`/`complexity`/`pin` when they apply).\n"
    "3. `call_agent` only when you are imperatively naming the exact agent to "
    "run next (with `agent_name`, plus optional `callback`/`complexity`) "
    "instead of letting the workflow resolve it.\n"
    "Put no deliverable prose outside the `finish` message."
)

_REPORT_TURN_TIMEOUT_S = 240.0

#: Engine-elicited finalize turn (issue #973). A step that declares an
#: ``output:`` shape ends with exactly two artifacts: the report as the turn's
#: markdown text (section bodies between the declared markers) and the routing
#: decision in the ``finalize`` tool's typed ``route`` argument.
_FINALIZE_PROMPT = (
    "Engine finalize turn - the work phase is over; do not call any work tool "
    "(no terminal, file_editor, think, ...). Reply with your report as the "
    "markdown text of this turn, then call the `finalize` tool with your "
    "routing decision.\n\n"
    "{sections}\n\n"
    "Call `finalize` with `route.appreciation` ('approved' when the work meets "
    "this step's goal, 'rejected' when it does not, 'hold' when a human must "
    "decide), plus `route.hold_kind` when holding, `route.required_agent` when "
    "an explicit agent must run at the routed step, and "
    "`route.round`/`route.note`/`route.complexity`/`route.pin` when they apply. "
    "Where the workflow goes next is engine topology, never your choice. Put no "
    "report prose in the tool arguments."
)

#: The control-plane tool names the report turn may expose (issue #830). The
#: report exists to fill the typed control slots, so the work tools are hidden
#: for its duration.
_ENGINE_TOOL_NAMES = frozenset(DEFAULT_ENGINE_TOOLS.tools)

#: The finalize turn's tool surface (issue #973): the terminal gate plus the
#: delegation tool and ``finish``. ``finish`` is exposed because the ``finalize``
#: observation says "You may now finish": without it the model cannot terminate
#: the turn and loops on repeated ``finalize`` calls (issue #1014). ``dispatch_next``
#: stays hidden — the route is typed.
_FINALIZE_TOOL_NAMES = frozenset({"finalize", "call_agent", "finish"})


class InProcessAgentHost:
    """Run the legacy agent assembly in-process and capture the deliverable.

    Hosting the run lets the engine observe the conversation's ordered event
    stream and freeze the agent's substantive answer at the first end-tool
    boundary (issue #740): :class:`~nebuko.engine.answer_capture.AnswerCapture`
    supersedes the legacy latest-wins ``latest_answer.md``, whose terminal
    control turn would otherwise clobber the deliverable.
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        load_pipeline: Callable[[], SimpleNamespace],
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._load_pipeline = load_pipeline
        self._env = env

    def run(self, prompt: str) -> dict[str, Any]:
        """Run ``prompt`` in-process and read the run artefacts back.

        Returns the mapping :func:`nebuko.engine.agent_runner.run_legacy_agent` returns,
        plus a ``deliverable`` key holding the captured answer. ``answer`` keeps
        its legacy value so routing/validation — which read the run's outcome
        from the workflow state, never from this text — are unchanged; only the
        posted body prefers ``deliverable``.

        Raises:
            EngineAgentUnavailable: Setup failed before the run began.
        """
        script = self._entry_point()
        ensure_import_path()
        pipeline = self._load_pipeline()

        with tempfile.TemporaryDirectory(prefix="gfa-agent-") as workdir:
            work = Path(workdir)
            # The temp dir is the cwd only for *artefact staging* (the legacy
            # steps write latest_answer.md / state here). Control-plane work that
            # must target the real checkout — the conversation workspace and the
            # wind-down git ops — is bound explicitly (issue #766).
            with _applied_env(
                _child_env(script, self._env, repo_root=self._repo_root)
            ), contextlib.chdir(workdir):
                with _capture_console() as console:
                    conversation = _run_pipeline(pipeline, prompt, self._repo_root, self)
                    console_log = console.getvalue()
            return self._capture_artifacts(work, conversation, console_log)

    def _entry_point(self) -> Path:
        """The frozen legacy entry point this host imports, or declare it out."""
        script = self._repo_root / _AGENT_SCRIPT
        if not script.is_file():
            raise EngineAgentUnavailable(
                f"legacy agent entry point not found: {script}"
            )
        return script

    def _capture_artifacts(
        self, work: Path, conversation: Any, console_log: str
    ) -> dict[str, Any]:
        """Read the run artefacts back, adding the captured deliverable.

        ``answer`` stays the legacy ``latest_answer.md`` value: a captured
        deliverable may legitimately carry a stale ``AGENT_RUN_CONFIG`` echo on
        a different turn, and the posted body is the new ``deliverable`` key
        (issue #740).
        """
        capture = AnswerCapture()
        for event in getattr(getattr(conversation, "state", None), "events", None) or []:
            capture.on_event(event)

        report = _report_payload(conversation)
        if report is not None:
            capture.freeze_report_payload(report)

        raw_answer = _read_text(work / _LATEST_ANSWER_FILE)
        # A step that declares an ``output:`` shape (issue #973) posts the
        # engine-rendered comment, not the agent's raw turn text.
        finalize_body = getattr(conversation, _FINALIZE_BODY_ATTR, None)
        deliverable = finalize_body or capture.deliverable(raw_answer)
        snapshot = capture.snapshot
        if deliverable != raw_answer:
            log.info(
                "[phase:agent] captured deliverable at the %s boundary (%d chars, "
                "latest_answer.md was %d)",
                snapshot.end_tool if snapshot else "none",
                len(deliverable),
                len(raw_answer),
            )
        state = _load_state(work / _STATE_FILE)
        result = {
            "answer": raw_answer,
            "deliverable": deliverable,
            "final_message": _read_text(work / _FINAL_MESSAGE_FILE),
            "state": state,
            "console_log": console_log or _read_text(work / _CONSOLE_LOG_FILE),
        }
        routing = _turn_outcome_payload(conversation, state)
        if routing:
            result["turn_outcome"] = routing
        return result

    def _elicit_report(self, conversation: Any) -> None:
        """Take the hand: one engine-elicited report turn, then harvest (issue #830).

        The work loop has ended; the engine starts a final LLM request that
        targets the control-plane slots and records the harvested payloads on
        the conversation (mirroring how ``run_agent`` stows ``run_outcome``).
        Best-effort: a failed or absent turn leaves the capture on its fixed
        fallback chain and never aborts the run.
        """
        if conversation is None:
            return
        contract = self._step_output_contract()
        if contract is not None:
            self._elicit_finalize(conversation, contract)
            return
        setattr(conversation, _REPORT_OFFSET_ATTR, _event_count(conversation))
        try:
            with _report_tools_only(conversation):
                _send_and_run(conversation, _REPORT_PROMPT)
        except Exception as error:  # noqa: BLE001 - degrade to the fallback chain
            log.warning("[phase:agent] report turn failed: %s", error)
            return
        finish = self._call_finish(conversation)
        routing = self._call_dispatch(conversation)
        handoff = self._call_agent(conversation)
        if finish is not None:
            setattr(conversation, _REPORT_PAYLOAD_ATTR, finish)
            log.info(
                "[phase:agent] report turn harvested finish.message (%d chars)",
                len(finish.fields.get("message", "") or ""),
            )
        if routing is not None:
            setattr(conversation, _REPORT_ROUTING_ATTR, routing)
        if handoff is not None:
            setattr(conversation, _REPORT_FUNCTION_CALL_ATTR, handoff)
        if finish is None and routing is None and handoff is None:
            log.warning("[phase:agent] report turn returned no control-plane call")

    def _call_finish(self, conversation: Any) -> EngineToolPayload | None:
        """Engine-started report turn's ``finish`` payload: the final message."""
        return _harvest_report(conversation, "finish")

    def _call_dispatch(self, conversation: Any) -> EngineToolPayload | None:
        """Engine-started report turn's ``dispatch_next`` payload: the routing."""
        return _harvest_report(conversation, "dispatch_next")

    def _call_agent(self, conversation: Any) -> EngineToolPayload | None:
        """Engine-started report turn's ``call_agent`` payload: the hand-off."""
        return _harvest_report(conversation, "call_agent")

    def _step_output_contract(self) -> OutputContract | None:
        """The step's declared output shape, or ``None`` for a legacy step.

        The resolver hands the declaration over as ``STEP_OUTPUT`` (issue #973);
        a step that declares none keeps the ``finish``/``dispatch_next`` path.
        """
        raw = (self._env or {}).get("STEP_OUTPUT") or os.environ.get("STEP_OUTPUT")
        return step_output_contract(raw)

    def _elicit_finalize(self, conversation: Any, contract: OutputContract) -> None:
        """Take the hand for a step that declares an ``output:`` shape (#973).

        One engine-elicited turn carries both artifacts: the report as the
        turn's markdown text and the route in the ``finalize`` tool's typed
        argument. The engine extracts the declared sections, renders the posted
        comment and stows it as the deliverable. A malformed answer gets exactly
        one retry naming the offending field; a second failure salvages the
        completed sections and logs loudly (fix 7, #973).
        """
        setattr(conversation, _REPORT_OFFSET_ATTR, _event_count(conversation))
        prompt = _FINALIZE_PROMPT.format(sections=contract.prompt_block())
        rendered: list[str] = []
        terminator = RunTerminator(
            contract,
            post=rendered.append,
            # The route is recorded by the finalize tool's executor into the
            # run-scoped dispatch registry, which ``build_output`` reads; the
            # terminator's own hook stays a no-op so the route is applied once.
            apply_route=lambda route: None,
        )
        handler = FinalizeToolHandler(terminator)
        for attempt in (1, 2):
            try:
                with _tools_only(conversation, _FINALIZE_TOOL_NAMES):
                    _send_and_run(conversation, prompt)
            except Exception as error:  # noqa: BLE001 - degrade to the fallback chain
                # The turn broke mid-flight; the text it already emitted may
                # still carry the completed sections, so salvage rather than
                # dropping the report on the floor (issue #1014).
                log.warning(
                    "[phase:agent] finalize turn failed (%s); salvaging the "
                    "turn text seen so far",
                    error,
                )
                body = _salvage_finalize_body(handler, contract, conversation)
                if body:
                    rendered.append(body)
                break
            turn_text = _finalize_turn_text(conversation)
            route = _finalize_route(conversation) or RouteDecision(appreciation="hold")
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
                    body = _salvage_finalize_body(handler, contract, conversation)
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
            setattr(conversation, _FINALIZE_BODY_ATTR, rendered[-1])
            log.info(
                "[phase:agent] finalize turn rendered the posted comment (%d chars)",
                len(rendered[-1]),
            )
        else:
            # No section survived; the run silently degrades to the work-loop
            # ``finish`` note. Name it so the loss is diagnosable from console.txt
            # (issue #1014).
            log.warning(
                "[phase:agent] finalize turn rendered no comment; falling back "
                "to the captured deliverable (the work-loop finish note)"
            )


def _salvage_finalize_body(
    handler: FinalizeToolHandler, contract: OutputContract, conversation: Any
) -> str:
    """Render the completed sections of a broken finalize turn (issue #1014).

    ``salvage`` never raises; an empty result means no section survived, and the
    caller keeps the fallback chain.
    """
    report = handler.salvage(_finalize_turn_text(conversation))
    return OutputRenderer(contract).render(report.sections)


def _report_payload(conversation: Any) -> EngineToolPayload | None:
    """The report turn's harvested ``finish`` payload, if the turn produced one."""
    return getattr(conversation, _REPORT_PAYLOAD_ATTR, None)


def _turn_outcome_payload(conversation: Any, state: Any) -> dict[str, Any] | None:
    """The run's factual turn-outcome payload (issues #830, #964).

    The report turn replaces ``final_message.txt`` — the artefact the engine's
    ``route`` phase used to scrape for the legacy ``AGENT_RUN_CONFIG`` sentinel —
    so the outcome must be handed over structurally instead. Two report-turn
    channels feed this: the harvested ``dispatch_next`` payload (the agent's
    factual verdict: ``appreciation``/``hold_kind``/``required_agent``/``round``/
    ``note`` plus the prefs) and the harvested typed ``call_agent`` hand-off
    (issue #876) naming the exact next agent. A hand-off outranks the
    state-derived target so an imperative cross-agent call is never lost to a
    workflow-relative outcome. When the turn returns only ``finish``, the
    outcome ``build_output`` resolved into the run's workflow state (itself
    sourced from the typed tools) is used.
    """
    harvested = getattr(conversation, _REPORT_ROUTING_ATTR, None)
    handoff = getattr(conversation, _REPORT_FUNCTION_CALL_ATTR, None)
    direct = _drop_blanks(harvested.fields) if harvested is not None else {}
    if direct or handoff is not None:
        # The harvested fields win per key, but the report turn routinely
        # omits the cross-agent target: that lives only in the work loop's
        # resolution. Merge the state fallback underneath so an omitted key
        # inherits it instead of silently reverting the hand-off to the
        # workflow's default next step (issue #830). The typed ``call_agent``
        # target then overrides the state-derived target (issue #876).
        fallback = _state_turn_outcome_fields(state) or {}
        return {**fallback, **direct, **_handoff_outcome_fields(handoff)}
    if getattr(conversation, _FINALIZE_BODY_ATTR, None) is not None:
        # A step that declares an ``output:`` shape (issue #973) routes on the
        # typed ``finalize`` route, which the tool recorded into the run's
        # workflow state; there is no ``finish`` payload to key off.
        return _state_turn_outcome_fields(state)
    if _report_payload(conversation) is None:
        return None
    return _state_turn_outcome_fields(state)


def _handoff_outcome_fields(handoff: EngineToolPayload | None) -> dict[str, Any]:
    """A harvested report-turn ``call_agent`` as turn-outcome payload fields.

    The imperative hand-off names the exact next agent (``agent_name``), which
    the factual outcome carries as ``required_agent`` (issue #876). Returns
    ``{}`` when there is no hand-off, so it never disturbs the verdict.
    """
    if handoff is None:
        return {}
    fields = _drop_blanks(handoff.fields)
    mapped: dict[str, Any] = {}
    if fields.get("agent_name"):
        mapped["required_agent"] = fields["agent_name"]
    if fields.get("complexity"):
        mapped["complexity"] = fields["complexity"]
    return mapped


def _drop_blanks(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Populated entries only, so a blank argument never overrides a fallback."""
    return {key: value for key, value in fields.items() if value}


def _state_turn_outcome_fields(state: Any) -> dict[str, Any] | None:
    """The workflow state's recorded turn outcome as payload fields."""
    outcome = getattr(state, "turn_outcome", None)
    if outcome is None:
        return None
    prefs = getattr(state, "prefs", None)
    fields: dict[str, Any] = {
        "status": outcome.status.value if outcome.status else "",
        "appreciation": outcome.appreciation.value if outcome.appreciation else "",
        "hold_kind": outcome.hold_kind.value if outcome.hold_kind else "",
        "required_agent": outcome.required_agent or "",
        "note": outcome.note or "",
        "complexity": (getattr(prefs, "complexity", "") or "") if prefs else "",
    }
    if outcome.incomplete_reason:
        fields["incomplete_reason"] = outcome.incomplete_reason.value
    return _drop_blanks(fields) or None


def _harvest_report(conversation: Any, tool_name: str) -> EngineToolPayload | None:
    """Harvest a typed control-plane payload from the report turn's events only."""
    offset = getattr(conversation, _REPORT_OFFSET_ATTR, 0) or 0
    events = _events(conversation)
    payload = None
    for event in events[offset:]:
        if getattr(event, "tool_name", None) != tool_name:
            continue
        harvested = DEFAULT_ENGINE_TOOLS.harvest(
            tool_name, getattr(event, "action", None)
        )
        if harvested is not None:
            payload = harvested
    return payload


@contextlib.contextmanager
def _report_tools_only(conversation: Any) -> Iterator[None]:
    """Restrict the report turn's tool surface to the control plane (issue #830)."""
    with _tools_only(conversation, _ENGINE_TOOL_NAMES):
        yield


@contextlib.contextmanager
def _tools_only(conversation: Any, allowed: frozenset[str]) -> Iterator[None]:
    """Restrict a control turn's tool surface to ``allowed`` (issues #830, #973).

    The report/finalize turn exists to fill the typed control slots, so the work
    tools must not be callable while it runs. The SDK resolves each step's
    toolset from the agent's private ``_tools`` map (read fresh per LLM call), so
    the host swaps in the allowed tools for the turn's duration and restores the
    original map afterwards — the same private seam the SDK's own runtime tool
    registration uses. Hosts that do not expose that map (or that would not be
    narrowed) are left untouched; the turn prompt still constrains the turn.
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


def _finalize_anchor_index(conversation: Any) -> int:
    """Index of the harvested ``finalize`` event, or ``-1`` when there is none.

    Both the report text and the route read from this single anchor: the last
    ``finalize`` action event at or after ``_REPORT_OFFSET_ATTR``. Events after
    the ``finalize`` call (a trailing status message, a repeated call) can never
    move the anchor, so the posted body and the recorded route always come from
    the same turn (issue #1014).
    """
    offset = getattr(conversation, _REPORT_OFFSET_ATTR, 0) or 0
    events = _events(conversation)
    for index in range(len(events) - 1, offset - 1, -1):
        if getattr(events[index], "tool_name", None) == "finalize":
            return index
    return -1


def _finalize_turn_text(conversation: Any) -> str:
    """Report text anchored to the harvested ``finalize`` event's turn.

    Precedence: the anchor ``finalize`` action event's ``thought``; else the
    nearest preceding agent message; never an event after that ``finalize``
    call. The report rides in the turn that calls ``finalize``, so it surfaces
    as the action event's ``thought`` (the SDK emits no standalone
    ``MessageEvent`` for a turn that also carries a tool call); the nearest
    preceding agent message is the fallback when the tool call and its text
    landed on different turns.
    """
    anchor = _finalize_anchor_index(conversation)
    if anchor < 0:
        return ""
    text = _content_text(getattr(_events(conversation)[anchor], "thought", None)).strip()
    if text:
        return text
    offset = getattr(conversation, _REPORT_OFFSET_ATTR, 0) or 0
    events = _events(conversation)
    for index in range(anchor - 1, offset - 1, -1):
        if getattr(events[index], "tool_name", None) is not None:
            continue
        message = getattr(events[index], "llm_message", None)
        if message is None:
            continue
        text = _content_text(getattr(message, "content", None)).strip()
        if text:
            return text
    return ""


def _finalize_route(conversation: Any) -> RouteDecision | None:
    """The typed route of the finalize turn's last ``finalize`` call (#973).

    Uses the same event anchor as :func:`_finalize_turn_text`, so a trailing
    event can never decouple the harvested report from its route (issue #1014).
    """
    anchor = _finalize_anchor_index(conversation)
    if anchor < 0:
        return None
    return route_from_action(getattr(_events(conversation)[anchor], "action", None))


def _content_text(content: object) -> str:
    """Join an SDK message content value into plain text (mirrors the capture)."""
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for item in content:  # type: ignore[union-attr]
        text = getattr(item, "text", None)
        parts.append(text if isinstance(text, str) else str(item))
    return "".join(parts)


def _send_and_run(conversation: Any, prompt: str) -> None:
    """Send ``prompt`` and run one bounded pass (report turn, issue #830).

    Runs ``conversation.run`` on a worker thread so a hung turn cannot consume
    the whole job budget: on timeout the engine interrupts the conversation,
    the turn ends, and the caller keeps the deterministic fallback chain.
    """
    conversation.send_message(prompt)
    worker = threading.Thread(target=conversation.run, daemon=True)
    worker.start()
    worker.join(_REPORT_TURN_TIMEOUT_S)
    if worker.is_alive():
        log.warning("[phase:agent] report turn exceeded its budget - interrupting")
        with contextlib.suppress(Exception):
            conversation.interrupt()
        worker.join(30.0)
        if worker.is_alive():
            # The worker outlived the interrupt; the report turn is degraded and
            # any further events it emits may leak into the harvest window. Log
            # loudly so the fallback chain is the documented outcome of a run
            # (issue #830 review).
            log.error(
                "[phase:agent] report turn worker still alive after interrupt "
                "+ 30s join - harvesting a partially completed turn"
            )


def _event_count(conversation: Any) -> int:
    return len(_events(conversation))


def _events(conversation: Any) -> list:
    return list(getattr(getattr(conversation, "state", None), "events", None) or [])


def _run_pipeline(
    pipeline: SimpleNamespace,
    prompt: str,
    workspace: Path,
    host: "InProcessAgentHost" | None = None,
) -> Any:
    """Drive the legacy steps (resolve -> run -> report -> metrics -> build).

    ``args`` mirrors the legacy entry point's argparse namespace; the facade
    steps treat it as informational (only ``prompt_location`` is read, and the
    prompt is passed explicitly here). ``workspace`` is the real checkout the
    agent must operate on, passed explicitly so the conversation does not bind
    to the host's temporary cwd (issue #766). ``host`` drives the engine-elicited
    report turn between the work loop and output building (issue #830).
    """
    args = SimpleNamespace(prompt_location=None)
    llm_config, resolved_prompt, agent_cfg = pipeline.resolve_llm_config(args, prompt)
    conversation = pipeline.run_agent(
        llm_config, resolved_prompt, args, agent_cfg=agent_cfg, workspace=workspace
    )
    if host is not None:
        host._elicit_report(conversation)
    metrics = pipeline.collect_and_save_metrics(conversation, agent_cfg)
    pipeline.build_output(
        conversation, metrics, args, prev_state=pipeline.load_prev_state_or_recover()
    )
    return conversation


@contextlib.contextmanager
def _capture_console() -> Iterator[io.StringIO]:
    """Capture in-process stdout/stderr, mirroring the legacy child's tee."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        yield buffer


@contextlib.contextmanager
def _applied_env(overlay: Mapping[str, str]) -> Iterator[None]:
    """Install ``overlay`` into ``os.environ`` for the duration of the block.

    The in-process assembly reads configuration from ``os.environ`` (there is no
    child process to hand an env mapping to), so the run's env is applied around
    it and the previous values restored afterwards.
    """
    saved = {key: os.environ.get(key, _MISSING) for key in overlay}
    os.environ.update(overlay)
    try:
        yield
    finally:
        for key, previous in saved.items():
            if previous is _MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous  # type: ignore[assignment]
