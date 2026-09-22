"""Deterministic script-step executor (issue #541).

Runs a repo-local script declared in ``workflow.yaml`` and feeds the
workflow's normal posting pipeline: writes ``final_message.txt`` (+ split
parts) and ``workflow_state.json`` so the existing prepare -> bot-marker ->
post chain runs untouched. A non-zero exit or timeout is a *failure result*,
not a crash: artifacts are still written and the process exits 0 so the GHA
job continues to posting.

Usage (GHA): ``python3 "$SCRIPTS/features/run_script/run_script.py"`` with the
resolver env contract: ``SCRIPT_PATH``, ``SCRIPT_ARGS``,
``SCRIPT_TIMEOUT_MINUTES``, ``WORKFLOW_NAME``, ``WORKFLOW_STEP``,
``TRIGGER_BODY``.
"""

import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_script.sub_domains.message_splitter.message_splitter import split_message
from config import parse_position_env
from workflow_state import Dispatch, build_state, extract_state_from_body, state_to_json

DEFAULT_TIMEOUT_MINUTES = 30
MAX_TIMEOUT_MINUTES = 60  # matches the GHA step hard cap (timeout-minutes: 60)
FINAL_MESSAGE_MAX_LEN = 10000
_FINAL_MESSAGE_FILE = "final_message.txt"
_STATE_FILE = "workflow_state.json"


# ── Config & safety ──────────────────────────────────────────────


def read_config(env):
    """Read and validate the executor env contract. Raises ValueError."""
    path = env.get("SCRIPT_PATH", "").strip()
    if not path:
        raise ValueError("SCRIPT_PATH is required")
    args_str = env.get("SCRIPT_ARGS", "") or ""
    timeout_raw = (env.get("SCRIPT_TIMEOUT_MINUTES", "") or "").strip()
    timeout = DEFAULT_TIMEOUT_MINUTES
    if timeout_raw:
        try:
            timeout = int(timeout_raw)
        except ValueError:
            raise ValueError(f"SCRIPT_TIMEOUT_MINUTES must be an integer, got {timeout_raw!r}")
        if timeout < 1:
            raise ValueError(f"SCRIPT_TIMEOUT_MINUTES must be >= 1, got {timeout}")
        timeout = min(timeout, MAX_TIMEOUT_MINUTES)
    return {
        "path": path,
        "args": args_str.split(),
        "timeout_minutes": timeout,
        "workflow": env.get("WORKFLOW_NAME", "") or "",
        "step": env.get("WORKFLOW_STEP", "") or "",
        "trigger_body": env.get("TRIGGER_BODY", "") or "",
    }


def validate_script_path(path: str, root: str) -> str:
    """Re-verify the schema safety rules at runtime (defense in depth).

    Rejects absolute paths, ``..`` segments, missing files, and escapes
    outside the checkout root via realpath containment. Returns the resolved
    absolute path on success. Raises ValueError otherwise.
    """
    if path.startswith("/"):
        raise ValueError(f"script path must be repo-relative (no leading '/'): {path}")
    if ".." in path.split("/"):
        raise ValueError(f"script path must not contain '..' segments: {path}")
    resolved = os.path.realpath(os.path.join(root, path))
    root_resolved = os.path.realpath(root)
    if resolved != root_resolved and not resolved.startswith(root_resolved + os.sep):
        raise ValueError(f"script path escapes the checkout root: {path}")
    if not os.path.isfile(resolved):
        raise ValueError(f"script not found: {path}")
    return resolved


# ── Execution ────────────────────────────────────────────────────


def execute_script(path: str, args: list[str], timeout_seconds: int, cwd: str) -> dict:
    """Run the script; capture output + exit code. Timeout is a failure result.

    Tries direct exec first; on PermissionError falls back to ``/bin/sh`` for
    ``.sh`` scripts (v1 convenience, no shell interpolation of args).
    """
    started = time.monotonic()
    command = [path] + args
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout_seconds)
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "timed_out": False,
            "duration_s": round(time.monotonic() - started, 1),
        }
    except PermissionError:
        if not path.endswith(".sh"):
            return {
                "exit_code": 126,
                "stdout": "",
                "stderr": f"Permission denied (not executable) and no shell fallback for non-.sh scripts: {path}",
                "timed_out": False,
                "duration_s": round(time.monotonic() - started, 1),
            }
        try:
            proc = subprocess.run(["/bin/sh", path] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout_seconds)
        except FileNotFoundError:
            return {
                "exit_code": 127,
                "stdout": "",
                "stderr": "/bin/sh not available for shell fallback",
                "timed_out": False,
                "duration_s": round(time.monotonic() - started, 1),
            }
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "timed_out": False,
            "duration_s": round(time.monotonic() - started, 1),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "stdout": _decode_partial(exc.stdout),
            "stderr": _decode_partial(exc.stderr),
            "timed_out": True,
            "duration_s": round(time.monotonic() - started, 1),
        }


def _decode_partial(data) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


# ── Comment formatting ───────────────────────────────────────────


def _fence_for(output: str) -> str:
    """Pick a backtick fence longer than any run embedded in the output."""
    longest = max((len(run) for run in re.findall(r"`+", output)), default=0)
    return "`" * max(3, longest + 1)


def format_comment(config: dict, outcome: dict) -> str:
    """Build the structured PASS/FAIL comment body for the script step."""
    banner = "PASS ✅" if outcome["exit_code"] == 0 and not outcome["timed_out"] else "FAIL ⚠️"
    lines = [
        f"**Script step `{config['step']}` — {banner}**",
        f"- Script: `{config['path']}`",
        f"- Args: `{(' '.join(config['args'])) if config['args'] else '(none)'}`",
        f"- Exit code: {outcome['exit_code']}",
        f"- Duration: {outcome['duration_s']}s",
    ]
    if outcome["timed_out"]:
        lines.append(f"- ⏱️ TIMED OUT after {config['timeout_minutes']} minutes")
    output = _combine_output(outcome["stdout"], outcome["stderr"])
    lines.append("")
    fence = _fence_for(output)
    lines.append(fence)
    lines.append(output if output else "(no output)")
    lines.append(fence)
    return "\n".join(lines)


def _combine_output(stdout: str, stderr: str) -> str:
    """Merge stdout/stderr for display (stderr follows stdout)."""
    parts = []
    if stdout:
        parts.append(stdout.rstrip("\n"))
    if stderr:
        parts.append(stderr.rstrip("\n"))
    return "\n".join(parts)


# ── Artifact writing ─────────────────────────────────────────────


def write_final_message(message: str) -> int:
    """Write final_message.txt (part 1) + overflow parts; returns part count."""
    parts = split_message(message, FINAL_MESSAGE_MAX_LEN)
    if not parts:
        parts = [""]
    with open(_FINAL_MESSAGE_FILE, "w", encoding="utf-8") as f:
        f.write(parts[0])
    for i, part in enumerate(parts[1:], start=2):
        with open(f"final_message_part_{i}.txt", "w", encoding="utf-8") as f:
            f.write(part)
    if len(parts) > 1:
        with open("final_message_parts_count.txt", "w", encoding="utf-8") as f:
            f.write(str(len(parts)))
    return len(parts)


def derive_phase(workflow_step: str) -> str:
    """Derive the state phase from the step name (mirrors build_output)."""
    if not workflow_step:
        return ""
    tokens = set(re.split(r"[_\-]", workflow_step.lower()))
    if tokens & {"analyze", "context"}:
        return "analysis"
    if "plan" in tokens:
        return "planning"
    if tokens & {"stub", "human_approval"}:
        return "stub"
    if "implement" in tokens:
        return "execution"
    if "review" in tokens:
        return "review"
    return ""


def write_workflow_state(config: dict, result: str) -> dict:
    """Advance the workflow state with a synthetic pass/fail dispatch.

    The previous state is taken from the trigger comment body (the agent
    query-context step does not run for script steps). Returns the parsed
    state dict (also written to ./workflow_state.json).
    """
    prev_state = extract_state_from_body(config["trigger_body"])
    summary = (
        f"Script step `{config['step']}` finished ({result}); "
        "output posted as a comment."
    )
    new_state = build_state(
        prev_state,
        workflow=config["workflow"],
        step=config["step"],
        agent="script",
        phase=derive_phase(config["step"]),
        summary=summary,
        comment_ids=[],
        result=result,
        dispatch=Dispatch(status="advance", branch=result),
        # v2 mount-chain position (issue #618): script runs inside a
        # sub-workflow post the full chain too (same env the agent path uses).
        position=parse_position_env(os.environ.get("WORKFLOW_POSITION", "")),
    )
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(state_to_json(new_state))
    return new_state.model_dump(mode="json", exclude_defaults=True)


# ── Entry point ──────────────────────────────────────────────────


def run() -> int:
    """Execute the script step; returns the process exit code (0 on result)."""
    root = os.getcwd()
    try:
        config = read_config(os.environ)
        script_abs = validate_script_path(config["path"], root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Running script step '{config['step']}': {script_abs} {config['args']}", flush=True)
    outcome = execute_script(script_abs, config["args"], config["timeout_minutes"] * 60, root)
    result = "pass" if (outcome["exit_code"] == 0 and not outcome["timed_out"]) else "fail"
    print(f"Script finished: exit={outcome['exit_code']} timed_out={outcome['timed_out']} -> {result}", flush=True)

    comment = format_comment(config, outcome)
    n_parts = write_final_message(comment)
    if n_parts > 1:
        print(f"Comment split into {n_parts} parts", flush=True)
    state = write_workflow_state(config, result)
    print(f"State written: step={state.get('step')} dispatch={state.get('dispatch')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
