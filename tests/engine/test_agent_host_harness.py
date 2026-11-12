"""The host delegates its closing turn to the injected channel (issue #1368)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nebuko.engine.agent_host import InProcessAgentHost
from nebuko.engine.finalize import TurnOutcomePayload
from nebuko.engine.harness import ClosingResult

ENTRY_POINT = (
    Path("src") / "nebuko" / "engine" / "lib" / "agent_script" / "agent_script.py"
)


class _Conversation:
    def __init__(self) -> None:
        self.id = "conv-delegation"
        self.state = SimpleNamespace(events=[])


class _Channel:
    def __init__(self, result: ClosingResult) -> None:
        self.result = result
        self.calls: list[Any] = []

    def elicit(self, session: Any, contract: Any) -> ClosingResult:
        self.calls.append((session, contract))
        return self.result


def _host(tmp_path: Path, channel: Any = None, session: Any = None):
    repo_root = tmp_path / "checkout"
    script = repo_root / ENTRY_POINT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# imported, never executed\n", encoding="utf-8")
    conversation = _Conversation()

    def _build_output(conversation, metrics, args, prev_state=None):
        Path("latest_answer.md").write_text("legacy answer", encoding="utf-8")
        Path("workflow_state.json").write_text(
            json.dumps({"version": 1, "workflow": "w", "step": "s", "agent": "dev"}),
            encoding="utf-8",
        )

    pipeline = SimpleNamespace(
        build_output=_build_output,
        collect_and_save_metrics=lambda conversation, agent_cfg: object(),
        load_prev_state_or_recover=lambda: None,
        resolve_llm_config=lambda args, prompt: (object(), prompt, object()),
        run_agent=lambda *a, **k: conversation,
    )
    host = InProcessAgentHost(
        repo_root=repo_root,
        load_pipeline=lambda: pipeline,
        session=session,
        closing_channel=channel,
    )
    return host, conversation


def test_host_delegates_closing_to_injected_channel(tmp_path: Path) -> None:
    result = ClosingResult(
        report="## Report\n\n- delegated",
        outcome=TurnOutcomePayload(appreciation="approved"),
    )
    channel = _Channel(result)
    session = object()
    host, conversation = _host(tmp_path, channel, session)

    outcome = host.run("prompt")

    assert len(channel.calls) == 1
    assert channel.calls[0][0] is session
    assert outcome["deliverable"] == "## Report\n\n- delegated"


def test_host_falls_back_to_openhands_without_an_injection(tmp_path: Path) -> None:
    host, _ = _host(tmp_path)
    assert host._closing_channel is None
    # The default pair is constructed lazily; the run must not raise.
    assert host.run("prompt")["deliverable"] == "legacy answer"
