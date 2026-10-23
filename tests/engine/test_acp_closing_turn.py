"""Closing turn of the Claude Code (ACP) backend.

Claude Code cannot call ``finalize``, so the engine reads the report and the
turn outcome out of one plain-text reply (``nebuko.engine.acp_finalize``) and
records the outcome into the same registries the tool's executor writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nebuko.engine.acp_finalize import (
    ACP_CLOSING_PROMPT,
    ACP_FENCE_TAG,
    parse_closing_reply,
)
from nebuko.engine.agent_host import InProcessAgentHost
from nebuko.engine.finalize import step_output_contract

from workflow_state import Appreciation

_CONVERSATION_ID = "conv-acp-closing"

STEP_OUTPUT_JSON = json.dumps(
    {"fields": [{"name": "headline", "title": True}, {"name": "changes"}]}
)


def _block(payload: dict[str, Any] | str) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return f"```{ACP_FENCE_TAG}\n{body}\n```"


class TestParseClosingReply:
    def test_splits_the_report_from_the_outcome_block(self) -> None:
        reply = parse_closing_reply(
            "## Done\n\nWired it.\n\n" + _block({"appreciation": "approved", "note": "ok"})
        )

        assert reply.report == "## Done\n\nWired it."
        assert reply.outcome is not None
        assert reply.outcome.appreciation == "approved"
        assert reply.outcome.note == "ok"
        assert reply.problem == ""

    def test_the_last_tagged_block_wins(self) -> None:
        reply = parse_closing_reply(
            _block({"appreciation": "rejected"})
            + "\nmore text\n"
            + _block({"appreciation": "approved"})
        )

        assert reply.outcome is not None
        assert reply.outcome.appreciation == "approved"

    def test_an_untagged_json_block_with_an_appreciation_is_accepted(self) -> None:
        reply = parse_closing_reply('report\n```json\n{"appreciation": "approved"}\n```')

        assert reply.outcome is not None
        assert reply.report == "report"

    def test_other_code_blocks_stay_in_the_report(self) -> None:
        text = "see:\n```python\nx = 1\n```\n" + _block({"appreciation": "approved"})

        reply = parse_closing_reply(text)

        assert "x = 1" in reply.report
        assert ACP_FENCE_TAG not in reply.report

    def test_the_agent_call_is_carried_through(self) -> None:
        reply = parse_closing_reply(
            "r\n"
            + _block(
                {
                    "appreciation": "approved",
                    "agent_call": {"agent_name": "context", "task": "collect"},
                }
            )
        )

        assert reply.outcome is not None
        assert reply.outcome.agent_call.agent_name == "context"

    @pytest.mark.parametrize(
        ("text", "fragment"),
        [
            ("just a report", "no `nebuko-turn-outcome` block"),
            ("r\n" + _block("{not json"), "Expecting"),
            ("r\n" + _block({"appreciation": "maybe"}), "appreciation"),
            ("r\n" + _block({"appreciation": "hold"}), "hold_kind is required"),
            (
                "r\n" + _block({"appreciation": "approved", "status": "completed"}),
                "status",
            ),
        ],
    )
    def test_an_unusable_block_reports_why_and_keeps_the_report(
        self, text: str, fragment: str
    ) -> None:
        reply = parse_closing_reply(text)

        assert reply.outcome is None
        assert fragment in reply.problem
        assert reply.report.startswith("r") or reply.report == "just a report"

    def test_the_prompt_formats_with_and_without_sections(self) -> None:
        plain = ACP_CLOSING_PROMPT.format(sections="")
        assert ACP_FENCE_TAG in plain
        assert '"appreciation": "approved"' in plain
        with_sections = ACP_CLOSING_PROMPT.format(sections="SECTION-SKELETON")
        assert "SECTION-SKELETON" in with_sections


# ── host-side closing turn ─────────────────────────────────────────────────


class ACPAgent:
    """Stands in for the SDK's ACP agent; the host matches on the class name."""


def _write_entry_point(repo_root: Path) -> None:
    script = (
        repo_root / "src" / "nebuko" / "engine" / "lib" / "agent_script" / "agent_script.py"
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# imported, never executed\n", encoding="utf-8")


def _user_message(text: str) -> object:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm.message import Message, TextContent

    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


def _finish_event(message: str) -> object:
    """The ``finish`` action the ACP agent emits at the end of each turn."""
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall
    from openhands.sdk.tool.builtins import FinishAction

    return ActionEvent(
        source="agent",
        tool_name="finish",
        thought=[],
        action=FinishAction(message=message),
        tool_call_id="tc-finish",
        tool_call=MessageToolCall(
            id="tc-finish", name="finish", arguments="{}", origin="completion"
        ),
        llm_response_id="r-finish",
    )


class _AcpConversation:
    """Replays one scripted Claude Code reply per ``run`` call."""

    def __init__(self, replies: list[str]) -> None:
        self.id = _CONVERSATION_ID
        self.agent = ACPAgent()
        self.state = SimpleNamespace(events=[_finish_event("work-loop note")])
        self._replies = list(replies)
        self.prompts: list[str] = []

    def send_message(self, prompt: str) -> None:
        self.prompts.append(prompt)
        self.state.events.append(_user_message(prompt))

    def run(self) -> None:
        if self._replies:
            self.state.events.append(_finish_event(self._replies.pop(0)))

    def interrupt(self) -> None:  # pragma: no cover - the timeout path
        pass


def _host_for(
    repo_root: Path, conversation: object, *, step_output: str | None = None
) -> InProcessAgentHost:
    def _run_agent(llm_config, prompt, args, agent_cfg=None, workspace=None):
        return conversation

    def _build_output(conversation, metrics, args, prev_state=None):
        Path("latest_answer.md").write_text("raw turn text", encoding="utf-8")
        Path("final_message.txt").write_text("finish note", encoding="utf-8")
        Path("workflow_state.json").write_text(
            json.dumps({"version": 1, "workflow": "w", "step": "s", "agent": "dev"}),
            encoding="utf-8",
        )

    pipeline = SimpleNamespace(
        build_output=_build_output,
        collect_and_save_metrics=lambda conversation, agent_cfg: object(),
        load_prev_state_or_recover=lambda: None,
        resolve_llm_config=lambda args, prompt: (object(), prompt, object()),
        run_agent=_run_agent,
    )
    env = {"STEP_OUTPUT": step_output} if step_output else {}
    return InProcessAgentHost(
        repo_root=repo_root, load_pipeline=lambda: pipeline, env=env
    )


@pytest.fixture(autouse=True)
def _clean_registries() -> None:
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from agent_script.features.call_agent_tool.call_agent_tool import (
        clear_run as clear_calls,
    )
    from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
        clear_run as clear_outcomes,
    )

    clear_outcomes(_CONVERSATION_ID)
    clear_calls(_CONVERSATION_ID)


class TestAcpClosingTurn:
    def test_the_report_is_the_deliverable_and_the_outcome_is_recorded(
        self, tmp_path: Path
    ) -> None:
        from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
            get_recorded_turn_outcome,
        )

        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _AcpConversation(
            ["## Report\n\nAll good.\n\n" + _block({"appreciation": "approved"})]
        )

        result = _host_for(repo_root, conversation).run("prompt")

        assert result["deliverable"] == "## Report\n\nAll good."
        assert result["turn_outcome"]["appreciation"] == "approved"
        outcome = get_recorded_turn_outcome(_CONVERSATION_ID)
        assert outcome is not None
        assert outcome.appreciation is Appreciation.APPROVED
        assert len(conversation.prompts) == 1
        assert ACP_FENCE_TAG in conversation.prompts[0]

    def test_an_agent_call_becomes_the_recorded_hand_off(self, tmp_path: Path) -> None:
        from agent_script.features.call_agent_tool.call_agent_tool import (
            get_recorded_function_call,
        )

        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _AcpConversation(
            [
                "report\n"
                + _block(
                    {
                        "appreciation": "approved",
                        "complexity": "high",
                        "agent_call": {"agent_name": "context", "task": "collect facts"},
                    }
                )
            ]
        )

        result = _host_for(repo_root, conversation).run("prompt")

        handoff = get_recorded_function_call(_CONVERSATION_ID)
        assert handoff is not None
        assert handoff.agent_name == "context"
        assert handoff.complexity == "high"
        assert result["turn_outcome"]["agent_call"]["agent_name"] == "context"

    def test_a_reply_without_an_outcome_gets_one_corrective_retry(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _AcpConversation(
            [
                "first draft with no block",
                "second draft\n" + _block({"appreciation": "rejected", "note": "nope"}),
            ]
        )

        result = _host_for(repo_root, conversation).run("prompt")

        assert len(conversation.prompts) == 2
        assert "no usable turn outcome" in conversation.prompts[1]
        assert result["deliverable"] == "second draft"
        assert result["turn_outcome"]["appreciation"] == "rejected"

    def test_two_unusable_replies_leave_the_report_and_no_outcome(
        self, tmp_path: Path
    ) -> None:
        from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
            get_recorded_turn_outcome,
        )

        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _AcpConversation(["still no block", "nor here"])

        result = _host_for(repo_root, conversation).run("prompt")

        assert result["deliverable"] == "nor here"
        assert "turn_outcome" not in result
        assert get_recorded_turn_outcome(_CONVERSATION_ID) is None

    def test_declared_sections_are_extracted_from_the_report(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        report = (
            "# Wired the format\n\n"
            "<!-- nebuko:section changes -->\n- a.py\n<!-- /nebuko:section -->\n"
        )
        conversation = _AcpConversation(
            [report + _block({"appreciation": "approved"})]
        )
        assert step_output_contract(STEP_OUTPUT_JSON) is not None

        result = _host_for(
            repo_root, conversation, step_output=STEP_OUTPUT_JSON
        ).run("prompt")

        assert result["deliverable"].startswith("# Wired the format")
        assert "## Changes" in result["deliverable"]
        assert result["turn_outcome"]["appreciation"] == "approved"

    def test_a_non_acp_conversation_keeps_the_finalize_tool_prompt(self) -> None:
        from nebuko.engine.agent_host import _FINALIZE_PROMPT, _closing_turn_for

        sdk_conversation = SimpleNamespace(agent=SimpleNamespace(_tools={}))

        assert _closing_turn_for(sdk_conversation).prompt == _FINALIZE_PROMPT
        assert _closing_turn_for(_AcpConversation([])).prompt == ACP_CLOSING_PROMPT
