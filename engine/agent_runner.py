"""Legacy agent-invocation seam for the engine (issue #590, Stage A).

The engine does not own an LLM runtime: :func:`run_legacy_agent` hands the
assembled prompt to the *existing* ``.github/scripts/agent_script`` entry point —
the same one the GHA path runs — and normalizes its output artefacts into the
mapping the driver's ``validate``/``publish``/``route`` phases consume.

Keeping the run behind the legacy entry point is what makes the driver
platform-free: swapping the adapter never touches the agent runtime, and hosts
(Stage B/C) or tests override ``Engine.invoke_agent`` when they already own it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from engine.paths import ensure_import_path

__all__ = ["run_legacy_agent"]

_AGENT_SCRIPT = Path(".github") / "scripts" / "agent_script" / "agent_script.py"
_LATEST_ANSWER_FILE = "latest_answer.md"
_FINAL_MESSAGE_FILE = "final_message.txt"
_STATE_FILE = "workflow_state.json"
_CONSOLE_LOG_FILE = "console.log"


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

    child_env = {**os.environ, **(env or {})}
    with tempfile.TemporaryDirectory(prefix="gfa-agent-") as workdir:
        work = Path(workdir)
        prompt_file = work / "final_prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, str(script), str(prompt_file)],
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


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load_state(path: Path) -> Any:
    if not path.is_file():
        return None
    ensure_import_path()
    from workflow_state.workflow_state import WorkflowState

    try:
        return WorkflowState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a malformed state is reported downstream
        return None
