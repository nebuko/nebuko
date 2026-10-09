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
from nebuko.engine.engine_tools import DEFAULT_ENGINE_TOOLS, EngineToolPayload
from nebuko.engine.paths import ensure_import_path

__all__ = ["InProcessAgentHost"]

log = logging.getLogger(__name__)

_MISSING = object()

#: Conversation attributes the report turn stows its results on (issue #830),
#: mirroring how ``run_agent`` stows ``run_outcome`` for ``build_output``.
_REPORT_PAYLOAD_ATTR = "nebuko_report_payload"
_REPORT_ROUTING_ATTR = "nebuko_report_routing"
_REPORT_OFFSET_ATTR = "nebuko_report_event_offset"

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
    "2. `dispatch_next` with your routing decision (status, and branch/"
    "complexity/reason when they apply).\n"
    "Put no deliverable prose outside the `finish` message."
)

_REPORT_TURN_TIMEOUT_S = 240.0

#: The control-plane tool names the report turn may expose (issue #830). The
#: report exists to fill the typed control slots, so the work tools are hidden
#: for its duration.
_ENGINE_TOOL_NAMES = frozenset(DEFAULT_ENGINE_TOOLS.tools)


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
        its legacy value so routing/validation — which read the dispatch sentinel
        out of it — are unchanged; only the posted body prefers ``deliverable``.

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

        ``answer`` stays the legacy ``latest_answer.md`` value: routing and
        validation read the ``AGENT_RUN_CONFIG`` sentinel out of it, and a
        captured deliverable may legitimately carry that sentinel on a different
        turn. The posted body is the new ``deliverable`` key (issue #740).
        """
        capture = AnswerCapture()
        for event in getattr(getattr(conversation, "state", None), "events", None) or []:
            capture.on_event(event)

        report = _report_payload(conversation)
        if report is not None:
            capture.freeze_report_payload(report)

        raw_answer = _read_text(work / _LATEST_ANSWER_FILE)
        deliverable = capture.deliverable(raw_answer)
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
        routing = _routing_payload(conversation, state)
        if routing:
            result["dispatch"] = routing
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
        setattr(conversation, _REPORT_OFFSET_ATTR, _event_count(conversation))
        try:
            with _report_tools_only(conversation):
                _send_and_run(conversation, _REPORT_PROMPT)
        except Exception as error:  # noqa: BLE001 - degrade to the fallback chain
            log.warning("[phase:agent] report turn failed: %s", error)
            return
        finish = self._call_finish(conversation)
        routing = self._call_dispatch(conversation)
        if finish is not None:
            setattr(conversation, _REPORT_PAYLOAD_ATTR, finish)
            log.info(
                "[phase:agent] report turn harvested finish.message (%d chars)",
                len(finish.fields.get("message", "") or ""),
            )
        if routing is not None:
            setattr(conversation, _REPORT_ROUTING_ATTR, routing)
        if finish is None and routing is None:
            log.warning("[phase:agent] report turn returned no control-plane call")

    def _call_finish(self, conversation: Any) -> EngineToolPayload | None:
        """Engine-started report turn's ``finish`` payload: the final message."""
        return _harvest_report(conversation, "finish")

    def _call_dispatch(self, conversation: Any) -> EngineToolPayload | None:
        """Engine-started report turn's ``dispatch_next`` payload: the routing."""
        return _harvest_report(conversation, "dispatch_next")


def _report_payload(conversation: Any) -> EngineToolPayload | None:
    """The report turn's harvested ``finish`` payload, if the turn produced one."""
    return getattr(conversation, _REPORT_PAYLOAD_ATTR, None)


def _routing_payload(conversation: Any, state: Any) -> dict[str, Any] | None:
    """The run's typed routing decision, in the engine's dispatch shape (#830).

    The report turn replaces ``final_message.txt`` — the artefact the engine's
    ``route`` phase scrapes for the legacy ``AGENT_RUN_CONFIG`` sentinel — so
    the routing must be handed over structurally instead. The report turn's
    harvested ``dispatch_next`` payload is the canonical channel; when the turn
    returns only ``finish``, the dispatch ``build_output`` resolved into the
    run's workflow state (itself sourced from the typed ``dispatch_next`` tool)
    is used. Runs without a report turn keep the legacy text scrape unchanged.
    """
    harvested = getattr(conversation, _REPORT_ROUTING_ATTR, None)
    if harvested is not None:
        direct = _drop_blanks(harvested.fields)
        if direct:
            return direct
    if _report_payload(conversation) is None:
        return None
    return _state_routing_fields(state)


def _drop_blanks(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Populated entries only, so a blank argument never overrides a fallback."""
    return {key: value for key, value in fields.items() if value}


def _state_routing_fields(state: Any) -> dict[str, Any] | None:
    """The workflow state's resolved dispatch as dispatch payload fields."""
    dispatch = getattr(state, "dispatch", None)
    if dispatch is None:
        return None
    return _drop_blanks(
        {
            "status": dispatch.status,
            "complexity": dispatch.complexity,
            "branch": dispatch.branch,
            "reason": dispatch.reason,
            "agent": dispatch.task_hint,
        }
    ) or None


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
    """Restrict the report turn's tool surface to the control plane (issue #830).

    The report turn exists to fill the typed control slots, so the work tools
    must not be callable while it runs. The SDK resolves each step's toolset
    from the agent's private ``_tools`` map (read fresh per LLM call), so the
    host swaps in the engine tools for the turn's duration and restores the
    original map afterwards — the same private seam the SDK's own runtime tool
    registration uses. Hosts that do not expose that map (or that would not be
    narrowed) are left untouched; the report prompt still constrains the turn.
    """
    agent = getattr(conversation, "agent", None)
    resolved = getattr(agent, "_tools", None)
    if not isinstance(resolved, dict) or not resolved:
        yield
        return
    narrowed = {
        name: tool for name, tool in resolved.items() if name in _ENGINE_TOOL_NAMES
    }
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
    log.info("[phase:agent] report turn tool surface restricted to %s", sorted(narrowed))
    try:
        yield
    finally:
        _install(resolved)


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
