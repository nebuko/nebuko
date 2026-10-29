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
import json
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
    turn_outcome_from_action,
    step_output_contract,
)
from nebuko.engine.paths import ensure_import_path
from nebuko.engine.report_text import (
    REPORT_OFFSET_ATTR as _REPORT_OFFSET_ATTR,
    closing_turn_report_text as _finalize_turn_text,
    events_of,
    finalize_anchor_index as _finalize_anchor_index,
)

__all__ = ["InProcessAgentHost"]

log = logging.getLogger(__name__)

_MISSING = object()

#: Conversation attribute the closing turn stows its results on (issues
#: #830, #1033), mirroring how ``run_agent`` stows ``run_outcome`` for
#: ``build_output``.
#: The rendered comment of a step that declares an ``output:`` shape (issue
#: #973). The finalize turn replaces the report turn for such a step, so the
#: deliverable is the engine-rendered comment, not a ``finish.message``.
_FINALIZE_BODY_ATTR = "nebuko_finalize_body"

#: Engine-elicited closing turn (issues #973, #1033). Every step ends through
#: the one terminal tool: the ``finalize`` call carries the report (its
#: ``report`` argument) and the typed ``turn_outcome``. An ``output:``
#: declaration only decides how the report is extracted and rendered.
_FINALIZE_PROMPT = (
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

#: Prepended to the closing prompt when the previous attempt called ``finalize``
#: with no report (issue #1038): the report is the call's ``report`` argument.
_FINALIZE_RETRY_PROMPT = (
    "Your previous finalize call carried no report, so nothing could be "
    "posted. Call `finalize` again with the full report in its `report` "
    "argument."
)

#: The closing turn's tool surface (issues #973, #1014): the terminal tool plus
#: ``finish``. ``finish`` stays exposed so the model can terminate the turn; it
#: is not a routing channel and its message is only a fallback deliverable. The
#: removed ``dispatch_next``/``call_agent`` are hidden — the outcome is typed.
_FINALIZE_TOOL_NAMES = frozenset({"finalize", "finish"})

#: Watchdog budget for one engine-elicited closing turn (seconds). A turn that
#: outlives it is interrupted and the caller keeps the deterministic fallback
#: chain (issues #830, #1033).
_CLOSING_TURN_TIMEOUT_S = 240.0


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
        caller_root: Path | None = None,
    ) -> None:
        # @stub-protected: signature defined by architect — do not modify
        """Bind the engine checkout and the caller's git anchor (issue #1208).

        ``repo_root`` is the engine checkout: it holds the ``agent_script``
        entry point and the assembly this host imports. ``caller_root`` is the
        caller's workspace, where the agent's git operations must land; it
        defaults to ``repo_root`` so a same-repo run keeps one root.
        """
        self._repo_root = Path(repo_root)
        self._load_pipeline = load_pipeline
        self._env = env
        self._caller_root = Path(caller_root) if caller_root is not None else Path(repo_root)

    def run(self, prompt: str) -> dict[str, Any]:
        # @stub-protected: signature defined by architect — do not modify
        """Run ``prompt`` in-process and read the run artefacts back.

        Returns the mapping :func:`nebuko.engine.agent_runner.run_legacy_agent` returns,
        plus a ``deliverable`` key holding the captured answer. ``answer`` keeps
        its legacy value so routing/validation — which read the run's outcome
        from the workflow state, never from this text — are unchanged; only the
        posted body prefers ``deliverable``.

        The entry point and assembly resolve from the engine checkout; the
        child env's ``GFA_REPO_ROOT`` and the conversation workspace are the
        caller's workspace (issue #1208).

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
            # wind-down git ops — is bound explicitly (issue #766), and it is the
            # *caller's* checkout, not the engine's (issue #1208).
            with _applied_env(
                _child_env(script, self._env, repo_root=self._caller_root)
            ), contextlib.chdir(workdir):
                with _capture_console() as console:
                    conversation = _run_pipeline(pipeline, prompt, self._caller_root, self)
                    console_log = console.getvalue()
            return self._capture_artifacts(work, conversation, console_log)

    def _entry_point(self) -> Path:
        # @stub-protected: signature defined by architect — do not modify
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
        """Take the hand: the one engine-elicited closing turn (issue #1033).

        Every step ends here. The work loop is over; the engine starts the final
        LLM request whose turn text is the report and whose ``finalize`` call
        carries the typed ``turn_outcome``. With a declared ``output:`` shape the
        declared sections are extracted and rendered into the posted comment;
        without one the turn text is the report verbatim. Best-effort: a failed
        or absent turn leaves the capture on its fixed fallback chain and never
        aborts the run.
        """
        if conversation is None:
            return
        self._elicit_finalize(conversation, self._step_output_contract())

    def _step_output_contract(self) -> OutputContract | None:
        """The step's declared output shape, or ``None`` for a step without one.

        The resolver hands the declaration over as ``STEP_OUTPUT`` (issue #973).
        It only selects the report *rendering* (issue #1033): a step that
        declares none ends through the same ``finalize`` turn and posts its raw
        turn text verbatim.
        """
        raw = (self._env or {}).get("STEP_OUTPUT") or os.environ.get("STEP_OUTPUT")
        return step_output_contract(raw)

    def _elicit_finalize(
        self, conversation: Any, contract: OutputContract | None
    ) -> None:
        """Run and harvest the closing ``finalize`` turn (issues #973, #1033).

        One engine-elicited turn carries both artifacts: the report as the
        turn's markdown text and the outcome in the ``finalize`` tool's typed
        ``turn_outcome``. A malformed section answer gets exactly one retry
        naming the offending field; a second failure salvages the completed
        sections and logs loudly (fix 7, #973).
        """
        setattr(conversation, _REPORT_OFFSET_ATTR, _event_count(conversation))
        prompt = _FINALIZE_PROMPT.format(
            sections=contract.prompt_block() if contract is not None else ""
        )
        if contract is None:
            # A closing turn that calls ``finalize`` with no report text is
            # retried once, naming the gap (issue #1038). Only when both turns
            # come back empty does the work-loop finish note become the post.
            text = ""
            for attempt in (1, 2):
                try:
                    _run_closing_turn(conversation, prompt)
                except Exception as error:  # noqa: BLE001 - fall back, never abort
                    # The turn never ran (or broke mid-flight); the work-loop
                    # capture chain stays the deliverable (issue #1014).
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
                    prompt = f"{_FINALIZE_RETRY_PROMPT}\n\n{prompt}"
            if text:
                setattr(conversation, _FINALIZE_BODY_ATTR, text)
                log.info(
                    "[phase:agent] finalize turn text is the report (%d chars)",
                    len(text),
                )
            else:
                log.error(
                    "[phase:agent] finalize turn returned no text twice; falling "
                    "back to the captured deliverable (the work-loop finish note)"
                )
            return
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
                _run_closing_turn(conversation, prompt)
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


def _turn_outcome_payload(conversation: Any, state: Any) -> dict[str, Any] | None:
    """The run's factual turn-outcome payload (issues #830, #964, #1033).

    The closing finalize turn is the only verdict channel: its typed
    turn_outcome (plus any agent_call) is applied by the tool executor
    into the run state, so the outcome build_output resolved there is the
    single source. status is engine-measured and is derived by
    :meth:`TurnOutcome.from_agent_payload`'s ``observed`` argument, never handed
    to the agent.

    ``build_output`` runs after this turn and is what projects the tool
    executor's recorded outcome into the state file; when that projection is
    missing (a build step that returned early, an in-process caller that
    bypasses it) the turn's own recorded outcome is read straight from the
    run-scoped registry instead of dropping the verdict.
    """
    if getattr(conversation, _FINALIZE_BODY_ATTR, None) is None and not _has_finalize(
        conversation
    ):
        return None
    return _state_turn_outcome_fields(state) or _recorded_turn_outcome_fields(
        conversation
    )


def _recorded_turn_outcome_fields(conversation: Any) -> dict[str, Any] | None:
    """The turn outcome the finalize executor recorded for this run, as fields."""
    conv_id = getattr(conversation, "id", None)
    if conv_id is None:
        return None
    ensure_import_path()
    from agent_script.features.dispatch_next_tool.dispatch_next_tool import (  # noqa: PLC0415
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
    from agent_script.features.call_agent_tool.call_agent_tool import (  # noqa: PLC0415
        get_recorded_function_call,
    )

    handoff = get_recorded_function_call(conv_id)
    if handoff is None:
        return None
    return {"agent_name": handoff.agent_name, "task": handoff.task}


def _has_finalize(conversation: Any) -> bool:
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


def _finalize_route(conversation: Any) -> RouteDecision | None:
    """The typed route of the finalize turn's last ``finalize`` call (#1033).

    The agent's ``turn_outcome`` is the single verdict channel; this reader
    keeps the lenient worker-side shape (:class:`RouteDecision`) for the local
    report-extraction call, which never routes. Uses the same event anchor as
    :func:`_finalize_turn_text`, so a trailing event can never decouple the
    harvested report from its route (issue #1014).
    """
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


def _run_closing_turn(conversation: Any, prompt: str) -> None:
    """Run one closing turn with the tool surface narrowed to the terminal tool."""
    with _tools_only(conversation, _FINALIZE_TOOL_NAMES):
        _send_and_run(conversation, prompt)


def _send_and_run(conversation: Any, prompt: str) -> None:
    """Send ``prompt`` and run one bounded pass (closing turn, issues #830, #1033).

    Runs ``conversation.run`` on a worker thread so a hung turn cannot consume
    the whole job budget: on timeout the engine interrupts the conversation,
    the turn ends, and the caller keeps the deterministic fallback chain.
    """
    conversation.send_message(prompt)
    worker = threading.Thread(target=conversation.run, daemon=True)
    worker.start()
    worker.join(_CLOSING_TURN_TIMEOUT_S)
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
    return len(events_of(conversation))


def _parse_context_bodies_env() -> dict[str, str]:
    """The run's cached thread-comment bodies from ``CONTEXT_BODIES`` (#748).

    The engine writes the map to a temp file and stamps the file's *path*
    into the child env before invoking the agent (issue #1193 - inlining the
    JSON directly overflowed ``tmux set-environment``'s message-size limit
    on threads with enough comment content); the in-process host reads the
    file back here so the configured ``fetch_comment_bodies`` tool's executor
    is bound to the actual thread. Absent/malformed values degrade to an
    empty map (lookups return ``not_found``) without aborting the run. The
    temp file is removed once read since nothing else owns it.
    """
    path = os.environ.get("CONTEXT_BODIES", "").strip()
    if not path:
        return {}
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        log.warning("Ignoring unreadable CONTEXT_BODIES path")
        return {}
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Ignoring malformed CONTEXT_BODIES file contents")
        return {}
    if not isinstance(payload, dict):
        log.warning("Ignoring malformed CONTEXT_BODIES file contents")
        return {}
    return {str(k): str(v) for k, v in payload.items()}


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
        llm_config,
        resolved_prompt,
        args,
        agent_cfg=agent_cfg,
        workspace=workspace,
        context_bodies=_parse_context_bodies_env(),
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
