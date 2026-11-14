"""In-process agent host for the engine (issue #740; #590 Stage A).

The engine hosts the legacy ``engine/lib/agent_script`` assembly *in-process*
so it can observe the conversation's ordered turn stream and freeze the agent's
real deliverable at the first end-tool boundary
(:class:`~nebuko.engine.answer_capture.AnswerCapture` — Option B of issue #740). The
frozen ``engine/lib`` package is only imported, never edited, so the capture
stays engine-only.

The closing turn is delegated to the injected
:class:`~nebuko.engine.harness.base.ClosingChannel` (issue #1368): the host
owns the run's artefacts, the harness owns how its runtime elicits the report
and the typed outcome. Without an injection the OpenHands pair is used, so
every legacy path is unchanged.

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
from nebuko.engine.finalize import OutputContract, step_output_contract
from nebuko.engine.harness.base import ClosingChannel, ClosingResult
from nebuko.engine.harness.openhands import (
    FINALIZE_BODY_ATTR as _FINALIZE_BODY_ATTR,
    FINALIZE_PROMPT as _FINALIZE_PROMPT,
    OpenHandsClosingChannel,
    finalize_route as _finalize_route,
    finalize_turn_text as _finalize_turn_text,
    salvage_finalize_body as _salvage_finalize_body,
    turn_outcome_payload as _turn_outcome_payload,
)
from nebuko.engine.report_text import REPORT_OFFSET_ATTR as _REPORT_OFFSET_ATTR

__all__ = ["InProcessAgentHost"]

log = logging.getLogger(__name__)

_MISSING = object()


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
        session: Any | None = None,
        closing_channel: ClosingChannel | None = None,
    ) -> None:
        # @stub-protected: signature defined by architect — do not modify
        """Bind the engine checkout and the caller's git anchor (issue #1208).

        ``repo_root`` is the engine checkout: it holds the ``agent_script``
        entry point and the assembly this host imports. ``caller_root`` is the
        caller's workspace, where the agent's git operations must land; it
        defaults to ``repo_root`` so a same-repo run keeps one root.

        ``session`` / ``closing_channel`` are the harness port's injection
        point (issue #1368): when absent the host drives the OpenHands
        conversation the legacy assembly returns.
        """
        self._repo_root = Path(repo_root)
        self._load_pipeline = load_pipeline
        self._env = env
        self._caller_root = (
            Path(caller_root) if caller_root is not None else Path(repo_root)
        )
        self._session = session
        self._closing_channel = closing_channel

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
        """Delegate the closing turn to the harness's channel (issue #1368)."""
        channel = (
            self._closing_channel
            or getattr(conversation, "harness_closing_channel", None)
            or OpenHandsClosingChannel()
        )
        session = (
            self._session
            or getattr(conversation, "harness_session", None)
            or conversation
        )
        try:
            result = channel.elicit(session, contract)
        except Exception as error:  # noqa: BLE001 - fall back, never abort
            log.warning(
                "[phase:agent] closing channel failed (%s); keeping the "
                "captured deliverable",
                error,
            )
            return
        self._apply_closing_result(conversation, result)

    def _apply_closing_result(
        self, conversation: Any, result: ClosingResult
    ) -> None:
        """Stow the channel's report on the conversation for the capture."""
        if result.report:
            setattr(conversation, _FINALIZE_BODY_ATTR, result.report)
        self._record_outcome(conversation, result.outcome)

    def _record_outcome(self, conversation: Any, outcome: Any) -> None:
        """Record a channel-supplied outcome the runtime did not record itself.

        OpenHands' ``finalize`` executor records into the run-scoped registries
        as it runs; a harness whose closing tool only fills its sink (Claude
        Code) lands here instead, so ``build_output`` reads one registry either
        way. An already-recorded outcome is left untouched — it carries the run
        prefs (complexity, pin) this path does not have.
        """
        if outcome is None:
            return
        conv_id = getattr(conversation, "id", None)
        if conv_id is None:
            return
        from nebuko.engine.lib.agent_script.features.dispatch_next_tool.dispatch_next_tool import (  # noqa: PLC0415
            get_recorded_turn_outcome,
            record_turn_outcome,
        )

        if get_recorded_turn_outcome(conv_id) is not None:
            return
        from nebuko.engine.lib.agent_script.features.call_agent_tool.call_agent_tool import (  # noqa: PLC0415
            record_function_call,
        )
        from nebuko.engine.lib.agent_script.features.finalize_tool.finalize_tool import (  # noqa: PLC0415
            outcome_to_prefs,
            outcome_to_turn_outcome,
        )

        record_turn_outcome(
            conv_id, outcome_to_turn_outcome(outcome), outcome_to_prefs(outcome)
        )
        handoff = outcome.to_function_call()
        if handoff is not None:
            record_function_call(conv_id, handoff)


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
    prev_state = pipeline.load_prev_state_or_recover()
    conversation = pipeline.run_agent(
        llm_config,
        resolved_prompt,
        args,
        agent_cfg=agent_cfg,
        workspace=workspace,
        context_bodies=_parse_context_bodies_env(),
        prev_state=prev_state,
    )
    if host is not None:
        host._elicit_report(conversation)
    metrics = pipeline.collect_and_save_metrics(conversation, agent_cfg)
    pipeline.build_output(
        conversation, metrics, args, prev_state=prev_state
    )
    return conversation


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
