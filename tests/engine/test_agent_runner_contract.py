"""Real-code-path coverage for the legacy agent seam (issue #590, review round 3).

The driver does not own an LLM runtime: :func:`nebuko.engine.agent_runner.run_legacy_agent`
spawns the *actual* ``engine/lib/agent_script/agent_script.py``. These tests
exercise that seam for real — entry point, child cwd, ``PYTHONPATH`` and artefact
names — without ever reaching the LLM (the real script is driven into its own
argument-validation error, and the artefact layout is proven with a stub entry
point written to the same relative path).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nebuko.engine.agent_runner import (
    EngineAgentUnavailable,
    _FINAL_MESSAGE_FILE,
    _LATEST_ANSWER_FILE,
    _STATE_FILE,
    _child_env,
    run_engine_agent,
    run_legacy_agent,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = (
    Path("src") / "nebuko" / "engine" / "lib" / "agent_script" / "agent_script.py"
)


def _stub_entry_point(script: Path) -> None:
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import json, pathlib, sys\n"
        "prompt = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        "pathlib.Path('latest_answer.md').write_text(prompt, encoding='utf-8')\n"
        "pathlib.Path('final_message.txt').write_text('final: ' + prompt, encoding='utf-8')\n"
        "state = {'version': 1, 'workflow': 'spec-driven-impl', 'step': 'stub', 'agent': 'dev'}\n"
        "pathlib.Path('workflow_state.json').write_text(json.dumps(state), encoding='utf-8')\n"
        "print('stub entry point ran from', pathlib.Path.cwd())\n",
        encoding="utf-8",
    )


def test_real_entry_point_imports_its_package_and_rejects_conflicting_prompts() -> None:
    """The real entry point must get past its imports under the seam's child env.

    ``PROMPT_STRING`` makes the script exit 1 on its own validation *after* it
    imported ``engine/lib/agent_script`` — so reaching that message proves the
    staged entry point plus ``PYTHONPATH`` reproduce the legacy invariant. Running
    the script in place instead fails earlier with
    ``cannot import name 'load_prompt' from 'agent_script'``.
    """
    with pytest.raises(RuntimeError) as excinfo:
        run_legacy_agent("prompt", repo_root=REPO_ROOT, env={"PROMPT_STRING": "conflict"})

    message = str(excinfo.value)
    assert "Both PROMPT_STRING and prompt_location provided" in message
    assert "cannot import name" not in message


def test_artifacts_are_read_back_from_the_child_working_directory(tmp_path: Path) -> None:
    """Answer, final message and state come from the child's cwd, by legacy name."""
    repo_root = tmp_path / "checkout"
    _stub_entry_point(repo_root / ENTRY_POINT)

    result: dict[str, Any] = run_legacy_agent("hello engine", repo_root=repo_root)

    assert result["answer"] == "hello engine"
    assert result["final_message"] == "final: hello engine"
    assert result["state"].run_id == 1
    assert result["state"].agent == "dev"
    assert "stub entry point ran from" in result["console_log"]


def test_state_file_missing_is_reported_as_none(tmp_path: Path) -> None:
    """A run that never wrote state must not invent one (review round 2, item 3)."""
    repo_root = tmp_path / "checkout"
    script = repo_root / ENTRY_POINT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path('latest_answer.md').write_text("
        "pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'), encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = run_legacy_agent("no state here", repo_root=repo_root)

    assert result["state"] is None
    assert result["final_message"] == ""


def test_artifact_names_match_the_legacy_readers() -> None:
    """The seam's file names are the ones the legacy host and scripts already use."""
    import nebuko.engine.lib.prepare_comment

    assert _LATEST_ANSWER_FILE == prepare_comment.LATEST_ANSWER_FILE

    build_output = (
        REPO_ROOT / "src/nebuko/engine/lib/agent_script/features/build_output/build_output.py"
    )
    build_output_source = build_output.read_text(encoding="utf-8")
    assert f'"{_FINAL_MESSAGE_FILE}"' in build_output_source
    assert f'"{_STATE_FILE}"' in build_output_source


# ── in-process seam (issue #740) ──────────────────────────────────────────


def test_child_env_pins_gfa_repo_root(tmp_path: Path) -> None:
    """#766: the child gets an explicit repo root so git/gh escape the temp cwd."""
    script = tmp_path / "checkout" / ENTRY_POINT

    env = _child_env(script, {"GFA_REPO_ROOT": "ignored-by-caller"})

    assert env["GFA_REPO_ROOT"] == str(tmp_path / "checkout")
    entries = [entry for entry in env.get("PYTHONPATH", "").split(os.pathsep) if entry]
    assert str(tmp_path / "checkout" / "src" / "nebuko" / "engine" / "lib") not in entries


def test_child_env_honours_an_explicit_repo_root(tmp_path: Path) -> None:
    """An explicit ``repo_root`` wins over the entry point's inferred location."""
    script = tmp_path / "checkout" / ENTRY_POINT
    explicit = tmp_path / "elsewhere"

    env = _child_env(script, None, repo_root=explicit)

    assert env["GFA_REPO_ROOT"] == str(explicit)


def _write_entry_point(repo_root: Path) -> None:
    """Create the entry point so the seam's existence check passes.

    The in-process seam imports the assembly, never executes this file, so its
    contents are irrelevant — only the path matters.
    """
    script = repo_root / ENTRY_POINT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# in-process seam: imported, never executed\n", encoding="utf-8")


def _agent_message(text: str) -> object:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message

    return MessageEvent(llm_message=Message(role="assistant", content=text), source="agent")


def _end_tool_turn(tool_name: str, text: str) -> object:
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    return ActionEvent(
        source="agent",
        tool_name=tool_name,
        thought=[TextContent(text=text)],
        tool_call_id=f"tc-{tool_name}",
        tool_call=MessageToolCall(
            id=f"tc-{tool_name}", name=tool_name, arguments="{}", origin="completion"
        ),
        llm_response_id="r1",
    )


def _finish_turn(text: str) -> object:
    return _end_tool_turn("finish", text)


def _finalize_turn(text: str) -> object:
    return _end_tool_turn("finalize", text)


def _install_fake_pipeline(
    monkeypatch,
    *,
    events: list[Any],
    latest_answer: str,
    run_agent=None,
) -> None:
    """Replace the legacy assembly with fakes that write real artefact files."""
    from nebuko.engine import agent_runner

    def _build_output(conversation, metrics, args, prev_state=None):
        Path("latest_answer.md").write_text(latest_answer, encoding="utf-8")
        Path("final_message.txt").write_text("finish note", encoding="utf-8")
        Path("workflow_state.json").write_text(
            json.dumps(
                {"version": 1, "workflow": "spec-driven-impl", "step": "s", "agent": "dev"}
            ),
            encoding="utf-8",
        )

    def _default_run_agent(llm_config, prompt, args, agent_cfg=None, workspace=None):
        return SimpleNamespace(state=SimpleNamespace(events=events))

    pipeline = SimpleNamespace(
        build_output=_build_output,
        collect_and_save_metrics=lambda conversation, agent_cfg: object(),
        load_prev_state_or_recover=lambda: None,
        resolve_llm_config=lambda args, prompt: (object(), prompt, object()),
        run_agent=run_agent or _default_run_agent,
    )
    monkeypatch.setattr(agent_runner, "_load_pipeline", lambda: pipeline)


def test_run_engine_agent_missing_entry_point_is_unavailable(tmp_path: Path) -> None:
    """A host without the legacy checkout must not crash — it declares itself out."""
    with pytest.raises(EngineAgentUnavailable):
        run_engine_agent("prompt", repo_root=tmp_path / "empty")


def test_run_engine_agent_returns_the_captured_deliverable(monkeypatch, tmp_path: Path) -> None:
    """The clobbered latest_answer.md is bypassed by the end-tool boundary capture."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    _install_fake_pipeline(
        monkeypatch,
        events=[_agent_message("Full architecture proposal"), _finish_turn("short note")],
        latest_answer="short note",
    )

    result = run_engine_agent("prompt", repo_root=repo_root)

    assert result["deliverable"] == "Full architecture proposal"
    # ``answer`` stays the legacy value: routing/validation read the dispatch
    # sentinel out of it (issue #740 must not reroute a run).
    assert result["answer"] == "short note"
    assert result["final_message"] == "finish note"
    assert result["state"].agent == "dev"


def test_run_engine_agent_recovers_a_deliverable_sharing_the_end_tool_turn(
    monkeypatch, tmp_path: Path
) -> None:
    """#555/#738: the deliverable shares the closing ``finalize`` turn, so it is
    not a ``MessageEvent``; the legacy ``latest_answer.md`` holds the trailing
    ``finish`` note. The capture recovers the shared-turn deliverable."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    _install_fake_pipeline(
        monkeypatch,
        events=[_finalize_turn("Full proposal"), _finish_turn("short note")],
        latest_answer="short note",
    )

    result = run_engine_agent("prompt", repo_root=repo_root)

    assert result["deliverable"] == "Full proposal"
    # ``answer`` stays the legacy value: routing/validation parse the dispatch
    # sentinel out of it (issue #740 must not reroute a run).
    assert result["answer"] == "short note"


def test_run_engine_agent_propagates_a_run_failure(monkeypatch, tmp_path: Path) -> None:
    """A failure after the run started is not a fallback signal (no double run)."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)

    def _boom(*args, **kwargs):
        raise RuntimeError("llm exploded")

    _install_fake_pipeline(monkeypatch, events=[], latest_answer="", run_agent=_boom)

    with pytest.raises(RuntimeError) as excinfo:
        run_engine_agent("prompt", repo_root=repo_root)

    assert not isinstance(excinfo.value, EngineAgentUnavailable)


def _engine(tmp_path: Path):
    from nebuko.adapters.local import LocalAdapter
    from nebuko.engine.models import Trigger
    from nebuko.engine.paths import repo_root as _repo_root
    from nebuko.engine.run_loop import Engine

    trigger = Trigger(author="olivier-norture", body="@@dev", type="issue_comment", number=740)
    adapter = LocalAdapter(tmp_path / "run", trigger=trigger)
    return Engine(adapter=adapter, repo_root=_repo_root(), env={})


def test_invoke_agent_prefers_the_in_process_seam(monkeypatch, tmp_path: Path) -> None:
    from nebuko.engine import agent_runner
    from nebuko.engine.run_loop import Engine

    monkeypatch.setattr(Engine, "_agent_env", lambda self: {})
    monkeypatch.setattr(
        agent_runner,
        "run_engine_agent",
        lambda prompt, *, repo_root, env: {"answer": "captured"},
    )

    assert _engine(tmp_path).invoke_agent("prompt") == {"answer": "captured"}


def test_invoke_agent_falls_back_when_the_seam_is_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    from nebuko.engine import agent_runner
    from nebuko.engine.run_loop import Engine

    def _unavailable(prompt, *, repo_root, env):
        raise EngineAgentUnavailable("no legacy assembly")

    monkeypatch.setattr(Engine, "_agent_env", lambda self: {})
    monkeypatch.setattr(agent_runner, "run_engine_agent", _unavailable)
    monkeypatch.setattr(
        agent_runner,
        "run_legacy_agent",
        lambda prompt, *, repo_root, env: {"answer": "legacy"},
    )

    assert _engine(tmp_path).invoke_agent("prompt") == {"answer": "legacy"}
