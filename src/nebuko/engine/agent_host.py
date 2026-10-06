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
from nebuko.engine.paths import ensure_import_path

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
                    conversation = _run_pipeline(pipeline, prompt, self._repo_root)
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
        return {
            "answer": raw_answer,
            "deliverable": deliverable,
            "final_message": _read_text(work / _FINAL_MESSAGE_FILE),
            "state": _load_state(work / _STATE_FILE),
            "console_log": console_log or _read_text(work / _CONSOLE_LOG_FILE),
        }


def _run_pipeline(pipeline: SimpleNamespace, prompt: str, workspace: Path) -> Any:
    """Drive the legacy steps (resolve -> run -> metrics -> build) in-process.

    ``args`` mirrors the legacy entry point's argparse namespace; the facade
    steps treat it as informational (only ``prompt_location`` is read, and the
    prompt is passed explicitly here). ``workspace`` is the real checkout the
    agent must operate on, passed explicitly so the conversation does not bind
    to the host's temporary cwd (issue #766).
    """
    args = SimpleNamespace(prompt_location=None)
    llm_config, resolved_prompt, agent_cfg = pipeline.resolve_llm_config(args, prompt)
    conversation = pipeline.run_agent(
        llm_config, resolved_prompt, args, agent_cfg=agent_cfg, workspace=workspace
    )
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
