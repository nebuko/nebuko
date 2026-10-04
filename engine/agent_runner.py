"""Agent-invocation seam for the engine (issue #590, Stage A; #740).

The engine does not own an LLM runtime: both seams reuse the *existing*
``.github/scripts/agent_script`` assembly — the same one the GHA path runs — and
normalize its output artefacts into the mapping the driver's
``validate``/``publish``/``route`` phases consume.

Two seams exist:

* :func:`run_engine_agent` hosts that assembly **in-process** (via
  :class:`~engine.agent_host.InProcessAgentHost`) so the engine can observe the
  conversation's ordered turn stream and capture the agent's real deliverable
  before the terminal control turns clobber it (issue #740, Option B). It is the
  preferred seam.
* :func:`run_legacy_agent` keeps the original subprocess boundary as a fallback
  for a host where the in-process assembly cannot start.

Keeping the run behind the legacy entry point is what makes the driver
platform-free: swapping the adapter never touches the agent runtime, and hosts
(Stage B/C) or tests override ``Engine.invoke_agent`` when they already own it.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from engine.paths import ensure_import_path

__all__ = ["EngineAgentUnavailable", "run_engine_agent", "run_legacy_agent"]

log = logging.getLogger(__name__)

_AGENT_SCRIPT = Path(".github") / "scripts" / "agent_script" / "agent_script.py"
_LATEST_ANSWER_FILE = "latest_answer.md"
_FINAL_MESSAGE_FILE = "final_message.txt"
_STATE_FILE = "workflow_state.json"
_CONSOLE_LOG_FILE = "console.log"
_PROMPT_FILE = "final_prompt.md"
# The entry point is staged under a name that cannot shadow the ``agent_script``
# package (see :func:`_stage_entry_point`).
_STAGED_ENTRY_FILE = "agent_script_entry.py"


def run_legacy_agent(
    prompt: str, *, repo_root: Path, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Run the legacy agent entry point for ``prompt`` and read back its artefacts.

    Args:
        prompt: The assembled agent prompt (written to a temp file).
        repo_root: Checkout holding ``.github/scripts/agent_script``.
        env: Extra environment for the child (LLM config, role, ...).

    Returns:
        Dict with ``answer`` (``latest_answer.md``), ``final_message``,
        ``state`` (``WorkflowState`` or ``None``) and ``console_log``.
    """
    script = Path(repo_root) / _AGENT_SCRIPT
    if not script.is_file():
        raise FileNotFoundError(f"legacy agent entry point not found: {script}")

    child_env = _child_env(script, env)
    with tempfile.TemporaryDirectory(prefix="gfa-agent-") as workdir:
        work = Path(workdir)
        prompt_file = work / _PROMPT_FILE
        prompt_file.write_text(prompt, encoding="utf-8")
        staged = _stage_entry_point(script, work)

        completed = subprocess.run(
            [sys.executable, str(staged), str(prompt_file)],
            cwd=workdir,
            env=child_env,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"agent_script exited {completed.returncode}: "
                f"{completed.stderr.strip()[-500:]}"
            )

        return {
            "answer": _read_text(work / _LATEST_ANSWER_FILE),
            "final_message": _read_text(work / _FINAL_MESSAGE_FILE),
            "state": _load_state(work / _STATE_FILE),
            "console_log": (completed.stdout or "") + (completed.stderr or ""),
        }


class EngineAgentUnavailable(RuntimeError):
    """The in-process agent seam could not start; a fallback seam may run.

    Raised only for *setup* failures (missing entry point, unimportable legacy
    assembly). A failure once the run has started propagates unchanged: falling
    back then would execute the agent a second time.
    """


def run_engine_agent(
    prompt: str, *, repo_root: Path, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Run the agent assembly in-process and capture the real deliverable.

    The in-process hosting lives in
    :class:`~engine.agent_host.InProcessAgentHost`; the driver keeps this seam so
    routing/validation (which read the dispatch sentinel out of the legacy
    ``answer``) are unchanged while the posted body prefers the captured
    ``deliverable`` (issue #740).

    Args:
        prompt: The assembled agent prompt.
        repo_root: Checkout holding ``.github/scripts/agent_script``.
        env: Extra environment for the run (LLM config, role, ...).

    Raises:
        EngineAgentUnavailable: Setup failed before the run began.
    """
    from engine.agent_host import InProcessAgentHost

    return InProcessAgentHost(
        repo_root=repo_root, load_pipeline=_load_pipeline, env=env
    ).run(prompt)


def _load_pipeline() -> SimpleNamespace:
    """Import the legacy agent assembly once, or declare the seam unavailable."""
    try:
        from agent_script import (  # type: ignore[import-not-found]
            build_output,
            collect_and_save_metrics,
            load_prev_state_or_recover,
            resolve_llm_config,
            run_agent,
        )
    except Exception as exc:  # noqa: BLE001 - setup failure, not a run failure
        raise EngineAgentUnavailable(
            f"cannot import the legacy agent assembly: {exc}"
        ) from exc
    return SimpleNamespace(
        build_output=build_output,
        collect_and_save_metrics=collect_and_save_metrics,
        load_prev_state_or_recover=load_prev_state_or_recover,
        resolve_llm_config=resolve_llm_config,
        run_agent=run_agent,
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _stage_entry_point(script: Path, work: Path) -> Path:
    """Copy the entry point out of its package directory before running it.

    The legacy host stages it the same way (``/tmp/agent_script.py``). Running it
    in place puts ``.github/scripts/agent_script`` first on the child's
    ``sys.path``, where ``agent_script.py`` shadows the ``agent_script`` package
    and ``from agent_script import load_prompt`` fails.
    """
    staged = work / _STAGED_ENTRY_FILE
    staged.write_bytes(script.read_bytes())
    return staged


def _child_env(script: Path, env: Mapping[str, str] | None) -> dict[str, str]:
    """Environment for the child, importing ``.github/scripts`` like the host."""
    child_env = {**os.environ, **(env or {})}
    scripts_dir = str(script.parent.parent)
    existing = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = f"{scripts_dir}{os.pathsep}{existing}" if existing else scripts_dir
    return child_env


def _load_state(path: Path) -> Any:
    if not path.is_file():
        return None
    ensure_import_path()
    from workflow_state.workflow_state import WorkflowState

    try:
        return WorkflowState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a malformed state is reported downstream
        return None
