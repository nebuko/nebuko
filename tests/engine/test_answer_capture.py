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


def _duck_end_tool(tool_name: str, *, thought: str = "", action: object = None) -> object:
    """A duck-typed control-plane turn carrying an engine-tool action.

    The leaf module reads the event structurally, so a plain object exercises
    the same typed-harvest path as a real SDK ``ActionEvent`` without the SDK's
    action-type validation (used for the routing-payload cases).
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        tool_name=tool_name,
        source="agent",
        thought=[SimpleNamespace(text=thought)] if thought else None,
        action=action,
    )



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


class TestTypedEngineToolPayloads:
    """#822: the deliverable is the engine tool's *typed* message, not prose."""

    def test_typed_finish_message_beats_the_turn_thought(self):
        """The #822 repro: the report is in ``finish.message``; the turn text is
        a one-liner. The typed payload must win."""
        from types import SimpleNamespace

        report = "## Wind-down\n\n- Rebased\n- Wrote feature X\n- Tests: 115 passed"
        capture = _capture(
            _duck_end_tool(
                "finish",
                thought="Work pushed and PR updated. Here is my wind-down summary.",
                action=SimpleNamespace(message=report),
            )
        )
        assert capture.deliverable() == report
        assert capture.snapshot.source == "work_loop_finish"

    def test_a_standalone_message_beats_a_work_loop_typed_message(self):
        """The documented contract holds: a standalone text answer wins over a
        work-loop ``finish.message`` (which may be only a status note)."""
        from types import SimpleNamespace

        capture = _capture(
            _msg_event("standalone deliverable"),
            _duck_end_tool("finish", action=SimpleNamespace(message="status note")),
        )
        assert capture.deliverable() == "standalone deliverable"

    def test_freeze_report_payload_is_authoritative(self):
        """The explicit report-turn freeze marks the source and wins outright."""
        from nebuko.engine.engine_tools import EngineToolPayload

        capture = _capture(_msg_event("work-loop draft"), _action_event("finish", "note"))
        capture.freeze_report_payload(
            EngineToolPayload(
                tool_name="finish", payload_kind="final_message", fields={"message": "Report via engine"}
            )
        )
        assert capture.deliverable() == "Report via engine"
        assert capture.snapshot.source == "report_finish"

    def test_a_routing_payload_is_never_a_deliverable(self):
        """``dispatch_next``'s typed fields are routing metadata, not a report —
        the shared-turn thought remains the captured deliverable."""
        from types import SimpleNamespace

        capture = _capture(
            _duck_end_tool(
                "dispatch_next",
                thought="Full proposal",
                action=SimpleNamespace(
                    status="hold", branch="", complexity="high", reason="awaiting review"
                ),
            )
        )
        assert capture.deliverable("fallback") == "Full proposal"

    def test_a_real_sdk_finish_action_is_harvested(self):
        from openhands.sdk.tool.builtins.finish import FinishAction

        capture = _capture(
            _duck_end_tool("finish", thought="note", action=FinishAction(message="Full report"))
        )
        assert capture.deliverable() == "Full report"
