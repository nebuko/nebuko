"""Unit tests for the in-process agent host (issue #740).

The host owns the in-process run: it imports the frozen legacy assembly, drives
its steps with a temporary cwd, and reads the run artefacts back with the
captured deliverable attached. These tests drive it with a fake pipeline so the
assembly never reaches the LLM, while still exercising the real code path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from engine.agent_host import InProcessAgentHost
from engine.agent_runner import EngineAgentUnavailable

ENTRY_POINT = Path(".github") / "scripts" / "agent_script" / "agent_script.py"


def _write_entry_point(repo_root: Path) -> None:
    """Create the entry point so the host's existence check passes.

    The host imports the assembly, never executes this file, so its contents are
    irrelevant — only the path matters.
    """
    script = repo_root / ENTRY_POINT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# imported, never executed\n", encoding="utf-8")


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


def _host(
    repo_root: Path,
    *,
    events: list[Any],
    latest_answer: str,
    run_agent: Any = None,
) -> InProcessAgentHost:
    """Build a host whose injected pipeline writes real artefact files."""

    def _build_output(conversation, metrics, args, prev_state=None):
        Path("latest_answer.md").write_text(latest_answer, encoding="utf-8")
        Path("final_message.txt").write_text("finish note", encoding="utf-8")
        Path("workflow_state.json").write_text(
            json.dumps({"version": 1, "workflow": "w", "step": "s", "agent": "dev"}),
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
    return InProcessAgentHost(repo_root=repo_root, load_pipeline=lambda: pipeline)


def test_missing_entry_point_declares_the_host_unavailable(tmp_path: Path) -> None:
    """A host without the legacy checkout must not crash — it declares itself out."""
    with pytest.raises(EngineAgentUnavailable):
        InProcessAgentHost(
            repo_root=tmp_path / "empty", load_pipeline=lambda: None
        ).run("prompt")


def test_captures_the_deliverable_from_the_end_tool_boundary(tmp_path: Path) -> None:
    """The clobbered latest_answer.md is bypassed by the boundary capture."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    host = _host(
        repo_root,
        events=[_agent_message("Full architecture proposal"), _end_tool_turn("finish", "short note")],
        latest_answer="short note",
    )

    result = host.run("prompt")

    assert result["deliverable"] == "Full architecture proposal"
    # ``answer`` stays the legacy value: routing/validation read the dispatch
    # sentinel out of it (issue #740 must not reroute a run).
    assert result["answer"] == "short note"
    assert result["final_message"] == "finish note"
    assert result["state"].agent == "dev"


def test_recovers_a_deliverable_sharing_the_end_tool_turn(tmp_path: Path) -> None:
    """#555/#738: the deliverable shares the ``dispatch_next`` turn, so it is not
    a ``MessageEvent``; the legacy ``latest_answer.md`` holds the finish note."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    host = _host(
        repo_root,
        events=[_end_tool_turn("dispatch_next", "Full proposal"), _end_tool_turn("finish", "note")],
        latest_answer="note",
    )

    result = host.run("prompt")

    assert result["deliverable"] == "Full proposal"
    assert result["answer"] == "note"


def test_falls_back_to_the_legacy_answer_for_an_empty_stream(tmp_path: Path) -> None:
    """No agent turn to classify → the legacy latest answer is the deliverable."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    host = _host(repo_root, events=[], latest_answer="legacy answer")

    assert host.run("prompt")["deliverable"] == "legacy answer"


def test_a_run_failure_propagates_and_is_not_an_unavailable_signal(tmp_path: Path) -> None:
    """A failure after the run started must not trigger a second (fallback) run."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)

    def _boom(*args, **kwargs):
        raise RuntimeError("llm exploded")

    host = _host(repo_root, events=[], latest_answer="", run_agent=_boom)

    with pytest.raises(RuntimeError) as excinfo:
        host.run("prompt")

    assert not isinstance(excinfo.value, EngineAgentUnavailable)


def test_run_binds_the_explicit_repo_root_workspace(tmp_path: Path) -> None:
    """#766: the agent operates on the real checkout, not the host's temp cwd.

    Artefact staging still runs under a temporary cwd, but the conversation
    workspace — and therefore the wind-down's git ops — must target the repo.
    """
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    seen: dict[str, Any] = {}

    def _capture_run_agent(llm_config, prompt, args, agent_cfg=None, workspace=None):
        seen["workspace"] = workspace
        seen["cwd"] = os.getcwd()
        return SimpleNamespace(state=SimpleNamespace(events=[]))

    host = _host(repo_root, events=[], latest_answer="", run_agent=_capture_run_agent)

    host.run("prompt")

    assert os.fspath(seen["workspace"]) == os.fspath(repo_root)
    assert os.fspath(seen["cwd"]) != os.fspath(repo_root)
