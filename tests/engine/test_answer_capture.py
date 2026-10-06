"""Unit tests for engine-owned answer capture (issue #740).

The capture layer is pure over the conversation's ordered event stream: these
tests drive it with real OpenHands SDK events (the same objects the run emits)
so the classification matches production, and with duck-typed fakes so the leaf
module stays importable without the SDK.
"""

from __future__ import annotations

from nebuko.engine.answer_capture import AnswerCapture, AnswerSnapshot


def _msg_event(text: str, *, source: str = "agent") -> object:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message

    return MessageEvent(llm_message=Message(role="assistant", content=text), source=source)


def _user_event(text: str) -> object:
    return _msg_event(text, source="user")


def _action_event(tool_name: str, text: str = "") -> object:
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    tool_call = MessageToolCall(
        id=f"tc-{tool_name}", name=tool_name, arguments="{}", origin="completion"
    )
    return ActionEvent(
        source="agent",
        tool_name=tool_name,
        thought=[TextContent(text=text)],
        tool_call_id=f"tc-{tool_name}",
        tool_call=tool_call,
        llm_response_id="r1",
    )


def _capture(*events: object, end_tools: frozenset[str] | None = None) -> AnswerCapture:
    capture = AnswerCapture() if end_tools is None else AnswerCapture(end_tools)
    for event in events:
        capture.on_event(event)
    return capture


class TestDeliverableSelection:
    def test_agent_message_is_the_deliverable(self):
        capture = _capture(_msg_event("  Full report  "))
        assert capture.deliverable() == "Full report"
        assert capture.snapshot is None

    def test_finish_note_does_not_clobber_an_earlier_message(self):
        """The #738 repro: deliverable then a terminal finish status note."""
        capture = _capture(_msg_event("Full architecture proposal"), _action_event("finish", "short note"))
        assert capture.deliverable() == "Full architecture proposal"

    def test_dispatch_next_note_does_not_clobber_an_earlier_message(self):
        capture = _capture(_msg_event("Full report"), _action_event("dispatch_next", "routing"))
        assert capture.deliverable() == "Full report"

    def test_latest_message_before_the_boundary_wins(self):
        capture = _capture(
            _msg_event("draft"), _msg_event("final deliverable"), _action_event("finish", "note")
        )
        assert capture.deliverable() == "final deliverable"

    def test_answer_sharing_the_end_tool_turn_is_captured(self):
        """#555: an assistant response with text + a tool call emits no
        MessageEvent, so the end-tool turn's own thought is the deliverable."""
        capture = _capture(_action_event("dispatch_next", "deliverable on the tool turn"))
        assert capture.deliverable("legacy fallback") == "deliverable on the tool turn"

    def test_shared_turn_deliverable_survives_a_trailing_finish_note(self):
        """#738/#555: the deliverable shares the dispatch_next turn; a later
        finish note must not clobber it (legacy latest-wins would)."""
        capture = _capture(
            _action_event("dispatch_next", "Full proposal"),
            _action_event("finish", "short note"),
        )
        assert capture.deliverable("short note") == "Full proposal"

    def test_registered_message_wins_over_the_boundary_turn_text(self):
        capture = _capture(
            _msg_event("standalone deliverable"), _action_event("finish", "note")
        )
        assert capture.deliverable("legacy") == "standalone deliverable"

    def test_empty_stream_returns_the_end_tool_answer(self):
        assert _capture().deliverable("legacy") == "legacy"

    def test_finish_only_run_posts_the_finish_text(self):
        capture = _capture(_action_event("finish", "status note"))
        assert capture.deliverable("status note") == "status note"


class TestBoundaryFreezing:
    def test_first_end_tool_wins_over_injected_later_turns(self):
        """#558: an engine-injected recall turn cannot re-freeze the answer."""
        capture = _capture(
            _msg_event("pre-recall deliverable"),
            _user_event("DISPATCH_RECALL_PROMPT"),
            _action_event("dispatch_next", "recall routing note"),
        )
        assert capture.deliverable() == "pre-recall deliverable"

    def test_snapshot_records_the_boundary(self):
        capture = _capture(_msg_event("report"), _action_event("finish", "note"))
        assert capture.snapshot == AnswerSnapshot(text="report", event_index=1, end_tool="finish")

    def test_no_snapshot_when_no_end_tool_ran(self):
        assert _capture(_msg_event("report")).snapshot is None

    def test_frozen_answer_ignores_a_later_real_message(self):
        capture = _capture(_msg_event("report"), _action_event("finish", "note"), _msg_event("late"))
        assert capture.deliverable() == "report"

    def test_custom_end_tools_replace_the_default_set(self):
        capture = _capture(_msg_event("report"), _action_event("finish", "note"), end_tools=frozenset())
        assert capture.snapshot is None
        assert capture.deliverable() == "report"


class TestNonDeliverableTurns:
    def test_user_message_is_never_captured(self):
        assert _capture(_user_event("from the human")).deliverable("fallback") == "fallback"

    def test_tool_call_thought_is_not_a_standalone_deliverable(self):
        assert _capture(_action_event("terminal", "rm -rf /")).deliverable("fallback") == "fallback"

    def test_blank_message_does_not_register(self):
        capture = _capture(_msg_event("   \n  "), _msg_event("real answer"))
        assert capture.deliverable() == "real answer"


class TestPlatformFreeDuckTyping:
    def test_classifies_plain_objects_without_importing_the_sdk(self):
        class _Fake:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        capture = _capture(
            _Fake(source="agent", llm_message=_Fake(content=[_Fake(text="report")])),
            _Fake(tool_name="finish"),
        )
        assert capture.deliverable() == "report"
