"""Wiring tests for the declared step-output format (issue #973 → #981).

The DSL and the terminal contract landed in PR #980 with their own unit tests;
these cover the *wiring*: the ``finalize`` tool registration, the tool-layer
retry policy, the run-loop/host harvest of an output step, and the
``default-with-output`` workflow that exercises the whole path.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from nebuko.engine.agent_host import (
    _FINALIZE_BODY_ATTR,
    _FINALIZE_PROMPT,
    _REPORT_OFFSET_ATTR,
    InProcessAgentHost,
)
from nebuko.engine.engine_tools import (
    DEFAULT_ENGINE_TOOLS,
    FinalizeToolError,
    FinalizeToolHandler,
)
from nebuko.engine.finalize import (
    OutputContract,
    OutputRenderer,
    RunTerminator,
    SectionExtractionError,
    TurnOutcomePayload,
    step_output_contract,
)

from workflow_state import Appreciation

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_FILE = REPO_ROOT / ".nebuko" / "workflows" / "default-with-output.yaml"

STEP_OUTPUT_JSON = json.dumps(
    {
        "fields": [
            {"name": "headline", "title": True},
            {"name": "changes"},
            {"name": "verification", "required": False},
        ]
    }
)


def _contract() -> OutputContract:
    contract = step_output_contract(STEP_OUTPUT_JSON)
    assert contract is not None
    return contract


def _answer(headline: str = "Wired the format", changes: str = "- a.py") -> str:
    return (
        f"# {headline}\n\n"
        "<!-- nebuko:section changes -->\n"
        f"{changes}\n"
        "<!-- /nebuko:section -->\n"
    )


class TestFinalizeToolRegistration:
    def test_finalize_is_the_terminal_gate(self) -> None:
        spec = DEFAULT_ENGINE_TOOLS.tools["finalize"]
        assert spec.payload_kind == "finalize"
        assert spec.payload_fields == ("turn_outcome",)
        assert DEFAULT_ENGINE_TOOLS.work_loop_presence("finalize") == "terminator"

    def test_finalize_tool_exposes_the_turn_outcome_only_schema(self) -> None:
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c1")[0]
        schema = tool.response_schema
        assert isinstance(schema, dict)
        assert list(schema["properties"]) == ["turn_outcome"]

    def test_finalize_tool_takes_the_report_as_an_argument(self) -> None:
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c-report")[0]
        action = tool.action_from_arguments(
            {"report": "## Plan\n\nthe change", "turn_outcome": {"appreciation": "approved"}}
        )

        assert action.report == "## Plan\n\nthe change"
        assert action.structured_output["turn_outcome"]["appreciation"] == "approved"

    def test_finalize_action_records_the_typed_turn_outcome(self) -> None:
        from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
            get_recorded_turn_outcome,
        )
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c-route")[0]
        action = tool.action_from_arguments(
            {"turn_outcome": {"appreciation": "approved"}}
        )
        observation = tool.executor(action)

        assert not observation.is_error
        outcome = get_recorded_turn_outcome("c-route")
        assert outcome is not None
        assert outcome.appreciation is Appreciation.APPROVED

    def test_the_agent_call_becomes_the_recorded_hand_off(self) -> None:
        """``agent_call`` is the only hand-off channel (issue #1033)."""
        from agent_script.features.call_agent_tool.call_agent_tool import (
            get_recorded_function_call,
        )
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c-handoff")[0]
        action = tool.action_from_arguments(
            {
                "turn_outcome": {
                    "appreciation": "approved",
                    "complexity": "high",
                    "agent_call": {"agent_name": "context", "task": "collect facts"},
                }
            }
        )
        tool.executor(action)

        handoff = get_recorded_function_call("c-handoff")
        assert handoff is not None
        assert handoff.agent_name == "context"
        assert handoff.complexity == "high"

    def test_a_missing_hold_kind_is_rejected_with_an_agent_facing_error(self) -> None:
        """Conditional rules are enforced at runtime (issue #1033)."""
        from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
            get_recorded_turn_outcome,
        )
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c-bad-hold")[0]
        action = tool.action_from_arguments({"turn_outcome": {"appreciation": "hold"}})
        observation = tool.executor(action)

        assert observation.is_error
        assert "hold_kind" in observation.text
        assert get_recorded_turn_outcome("c-bad-hold") is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"appreciation": "approved", "status": "completed"},
            {"appreciation": "approved", "required_agent": "review"},
            {"appreciation": "hold", "hold_kind": "awaiting_approval"},
        ],
    )
    def test_engine_owned_axes_are_rejected_at_the_schema(
        self, payload: dict
    ) -> None:
        """``status``, ``required_agent`` and ``awaiting_approval`` cannot enter.

        The SDK validates a typed ``response_schema`` before the executor runs,
        so the rejection surfaces as the SDK's validation error (which the agent
        loop turns into a tool error, ``agent.py``). The point under test is that
        the axis never reaches the outcome — not which layer says so.
        """
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c-reject")[0]
        with pytest.raises(ValueError):
            tool.action_from_arguments({"turn_outcome": payload})

    @pytest.mark.parametrize(
        "payload",
        [
            {"appreciation": "approved", "agent_call": {"agent_name": "  "}},
            {"appreciation": "approved", "complexity": "urgent"},
        ],
    )
    def test_unusable_agent_input_never_records_an_outcome(self, payload: dict) -> None:
        """A runtime rule violation is returned to the agent, never recorded."""
        from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
            get_recorded_turn_outcome,
        )
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c-reject")[0]
        observation = tool.executor(tool.action_from_arguments({"turn_outcome": payload}))

        assert observation.is_error
        assert get_recorded_turn_outcome("c-reject") is None

    def test_finalize_action_records_the_route_prefs(self) -> None:
        """The route's tier/pin travel with the outcome (issue #964 round 6)."""
        from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
            get_recorded_run_prefs,
        )
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c-route-prefs")[0]
        action = tool.action_from_arguments(
            {
                "turn_outcome": {
                    "appreciation": "approved",
                    "complexity": "high",
                    "pin": True,
                }
            }
        )
        tool.executor(action)

        prefs = get_recorded_run_prefs("c-route-prefs")
        assert prefs is not None
        assert prefs.complexity == "high"
        assert prefs.pin is True


class TestFinalizePromptTeachesTheTypedSchema:
    """The finalize prompt is agent-facing, so it must teach the live schema.

    A prompt naming deleted fields (``status``/``branch``/``reason``/``loop``)
    makes the model emit a route the tool rejects, so the terminal gate never
    records a verdict (issue #964).
    """

    def test_prompt_names_the_typed_fields(self) -> None:
        for field in ("appreciation", "hold_kind", "agent_call", "note"):
            assert field in _FINALIZE_PROMPT

    def test_prompt_names_every_verdict(self) -> None:
        for verdict in ("approved", "rejected", "hold"):
            assert verdict in _FINALIZE_PROMPT

    def test_prompt_teaches_no_deleted_field(self) -> None:
        for legacy in ("status", "branch", "reason", "loop", "required_agent"):
            assert legacy not in _FINALIZE_PROMPT


class TestFinalizeToolHandler:
    def _handler(self) -> tuple[FinalizeToolHandler, list[str]]:
        posted: list[str] = []
        terminator = RunTerminator(_contract(), post=posted.append)
        return FinalizeToolHandler(terminator), posted

    def test_renders_the_posted_comment_on_a_valid_answer(self) -> None:
        handler, posted = self._handler()

        report = handler.handle(
            TurnOutcomePayload(appreciation="approved"), _answer()
        )

        assert report.title == "Wired the format"
        assert report.sections["changes"] == "- a.py"
        assert posted and posted[0].startswith("# Wired the format")
        assert "## Changes" in posted[0]

    def test_first_failure_is_retryable_and_names_the_field(self) -> None:
        handler, posted = self._handler()

        with pytest.raises(FinalizeToolError) as excinfo:
            handler.handle(TurnOutcomePayload(appreciation="approved"), "# headline only\n")

        assert excinfo.value.retryable is True
        assert excinfo.value.field == "changes"
        assert posted == []

    def test_second_failure_fails_loudly(self) -> None:
        handler, _ = self._handler()
        with pytest.raises(FinalizeToolError):
            handler.handle(TurnOutcomePayload(appreciation="approved"), "# headline only\n")

        with pytest.raises(Exception) as excinfo:
            handler.handle(TurnOutcomePayload(appreciation="approved"), "# headline only\n")

        assert not isinstance(excinfo.value, FinalizeToolError)

    def test_salvage_never_raises_and_keeps_completed_sections(self) -> None:
        handler, _ = self._handler()

        report = handler.salvage(
            "# headline\n\n<!-- nebuko:section changes -->\n- a.py\n"
            "<!-- /nebuko:section -->\n"
        )

        assert report.title == "headline"
        assert report.sections["changes"] == "- a.py"


class TestStepOutputContract:
    def test_compiles_the_resolver_payload(self) -> None:
        contract = step_output_contract(STEP_OUTPUT_JSON)
        assert contract is not None
        assert [field.name for field in contract.fields] == [
            "headline",
            "changes",
            "verification",
        ]

    @pytest.mark.parametrize("raw", ["", None, "not json", "{}"])
    def test_absent_or_malformed_declaration_is_undeclared(self, raw: Any) -> None:
        assert step_output_contract(raw) is None


class TestDefaultWithOutputWorkflow:
    def test_the_workflow_file_is_resolved_by_name(self) -> None:
        from nebuko.engine.paths import ensure_import_path

        ensure_import_path()
        from config import load_workflow_with_dirs

        resolved = load_workflow_with_dirs(
            "default-with-output",
            str(REPO_ROOT / "defaults" / "workflows"),
            str(REPO_ROOT / ".nebuko" / "workflows"),
        )
        assert Path(resolved) == WORKFLOW_FILE

    def test_report_steps_declare_an_output_and_context_does_not(self) -> None:
        from nebuko.engine.paths import ensure_import_path

        ensure_import_path()
        from config import load_workflow

        workflow = load_workflow(str(WORKFLOW_FILE))
        declared = {step.name: step.output is not None for step in workflow.steps}
        assert declared == {
            "analyze": True,
            "context": False,
            "plan": True,
            "implement": True,
            "review": True,
        }

    def test_the_resolver_hands_the_declaration_to_the_run(self) -> None:
        from nebuko.engine.paths import ensure_import_path

        ensure_import_path()
        from resolve_config import resolve_config

        resolved = resolve_config(
            "/nebuko run workflow --name default-with-output",
            "prompts",
            str(REPO_ROOT / "defaults" / "workflows"),
            is_bot=False,
            local_workflows_dir=str(REPO_ROOT / ".nebuko" / "workflows"),
        )

        assert resolved["WORKFLOW_STEP"] == "analyze"
        contract = step_output_contract(resolved["STEP_OUTPUT"])
        assert contract is not None
        assert [field.name for field in contract.fields] == [
            "headline",
            "context_request",
            "assumptions",
        ]


# ── host-side finalize turn ────────────────────────────────────────────────


def _write_entry_point(repo_root: Path) -> None:
    script = (
        repo_root / "src" / "nebuko" / "engine" / "lib" / "agent_script" / "agent_script.py"
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# imported, never executed\n", encoding="utf-8")


def _finalize_event(
    text: str, route: dict[str, Any] | None = None, report: str = ""
) -> object:
    """A real ``finalize`` action event carrying the turn's report text."""
    from agent_script.features.finalize_tool.finalize_tool import FinalizeAction
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    action = FinalizeAction(report=report)
    if route is not None:
        action._structured_output = {"turn_outcome": route}
    return ActionEvent(
        source="agent",
        tool_name="finalize",
        thought=[TextContent(text=text)],
        action=action,
        tool_call_id="tc-finalize",
        tool_call=MessageToolCall(
            id="tc-finalize", name="finalize", arguments="{}", origin="completion"
        ),
        llm_response_id="r-finalize",
    )


def _agent_message_event(text: str) -> object:
    """A standalone agent ``MessageEvent`` (the fallback turn-text carrier)."""
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm.message import Message, TextContent

    return MessageEvent(
        source="agent",
        llm_message=Message(role="assistant", content=[TextContent(text=text)]),
    )


#: The conversation id the scripted conversation reports, so the finalize
#: executor's run-scoped registry entry and the harness read the same key.
_FAKE_CONVERSATION_ID = "conv-finalize-wiring"


def _run_recorded_finalize_actions(events: list[Any], conversation: Any) -> None:
    """Run the real ``finalize`` executor over the scripted actions (issue #1033).

    The SDK executes the tool after the turn; the scripted conversation replays
    the turn without the SDK, so the executor is invoked here to record the
    typed outcome in the run-scoped registry exactly as a live run would.
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from agent_script.features.finalize_tool.finalize_tool import FinalizeExecutor

    executor = FinalizeExecutor(conversation_id=_FAKE_CONVERSATION_ID)
    for event in events:
        if getattr(event, "tool_name", None) != "finalize":
            continue
        action = getattr(event, "action", None)
        if action is not None:
            executor(action, conversation)


def _engine_prompt_event(text: str) -> object:
    """The engine's own turn prompt as the SDK emits it (issue #1020).

    ``send_message`` becomes a ``user``-sourced ``MessageEvent`` in the
    conversation's event stream, so the engine's finalize prompt — which embeds
    ``OutputContract.prompt_block()`` — is a harvest candidate unless the reader
    filters message source.
    """
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm.message import Message, TextContent

    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


def _finalize_event_with_report(report: str, route: dict[str, Any] | None = None) -> object:
    """A ``finalize`` call whose ``report`` argument carries the deliverable."""
    return _finalize_event("", route, report=report)


def _rejected_finalize_event(arguments: dict[str, Any] | str) -> object:
    """A ``finalize`` call the SDK rejected: no action, only the raw arguments."""
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall

    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ActionEvent(
        source="agent",
        tool_name="finalize",
        thought=[],
        action=None,
        tool_call_id="tc-rejected",
        tool_call=MessageToolCall(
            id="tc-rejected", name="finalize", arguments=raw, origin="completion"
        ),
        llm_response_id="r-rejected",
    )


def _finalize_event_without_thought(route: dict[str, Any] | None = None) -> object:
    """A ``finalize`` call whose turn carried no report text of its own."""
    return _finalize_event("", route)


def _with_observations(events: list[Any]) -> list[Any]:
    """Follow each ``finalize`` call with its ``ObservationEvent``, as the SDK does.

    The SDK emits the observation right after the action, and it carries the same
    ``tool_name``: a harvest that matches on the name alone lands on it instead
    of on the call (issue #1038).
    """
    from agent_script.features.finalize_tool.finalize_tool import FinalizeObservation
    from openhands.sdk.event import ObservationEvent

    stream: list[Any] = []
    for event in events:
        stream.append(event)
        if getattr(event, "tool_name", None) != "finalize" or not hasattr(
            event, "tool_call"
        ):
            continue
        stream.append(
            ObservationEvent(
                source="environment",
                tool_name="finalize",
                tool_call_id=event.tool_call_id,
                observation=FinalizeObservation.from_text(text="Turn outcome recorded."),
                action_id=event.id,
            )
        )
    return stream


class _FakeAgent:
    def __init__(self, tools: dict[str, Any]) -> None:
        import threading

        self._tools = dict(tools)
        self._tools_lock = threading.RLock()


class _FinalizeConversation:
    """Replays one scripted finalize turn per ``run`` call."""

    def __init__(
        self,
        *,
        turns: list[list[Any]],
        tools: dict[str, Any] | None = None,
    ) -> None:
        self.id = _FAKE_CONVERSATION_ID
        self.state = SimpleNamespace(events=[])
        self.agent = _FakeAgent(tools or {})
        self._turns = list(turns)
        self.prompts: list[str] = []
        self.tools_seen: list[set[str]] = []

    def send_message(self, prompt: str) -> None:
        self.prompts.append(prompt)
        # The SDK records the prompt as a user ``MessageEvent`` (the turn start).
        self.state.events.append(_engine_prompt_event(prompt))

    def run(self) -> None:
        self.tools_seen.append(set(self.agent._tools))
        if self._turns:
            events = _with_observations(self._turns.pop(0))
            self.state.events.extend(events)
            _run_recorded_finalize_actions(events, self)

    def interrupt(self) -> None:  # pragma: no cover - the timeout path
        pass


def _host_for(
    repo_root: Path,
    conversation: object,
    *,
    latest_answer: str,
    step_output: str | None,
    state_turn_outcome: dict[str, Any] | None = None,
    classify_event: Callable[[object], bool] | None = None,
) -> InProcessAgentHost:
    def _run_agent(llm_config, prompt, args, agent_cfg=None, workspace=None):
        return conversation

    events = getattr(conversation, "state", SimpleNamespace(events=[])).events

    def _build_output(conversation, metrics, args, prev_state=None):
        Path("latest_answer.md").write_text(latest_answer, encoding="utf-8")
        Path("final_message.txt").write_text("finish note", encoding="utf-8")
        state: dict[str, Any] = {
            "version": 1,
            "workflow": "w",
            "step": "s",
            "agent": "dev",
        }
        if state_turn_outcome is not None:
            state["turn_outcome"] = state_turn_outcome
        # The real ``build_output`` writes the turn_outcome that the finalize
        # tool's executor recorded during the closing turn; a scripted
        # conversation bypasses that executor, so mirror it here (issue #1033).
        recorded = _recorded_turn_outcome_fields(conversation)
        if recorded is not None:
            state["turn_outcome"] = recorded
        Path("workflow_state.json").write_text(json.dumps(state), encoding="utf-8")
        # The real ``build_output`` rewrites ``latest_answer.md`` from the
        # conversation's events; a scripted conversation carries no run outcome,
        # so the events are replayed through the same scanner here (issue #1020).
        if classify_event is not None:
            import sys

            lib = (
                repo_root / "src" / "nebuko" / "engine" / "lib"
            )
            sys.path.insert(0, str(lib))
            try:
                from agent_script.features.build_output import build_output

                build_output._get_run_outcome = lambda *_: SimpleNamespace(
                    status=None, dispatch_recall_event_offset=None
                )
                build_output.extract_latest_answer(
                    list(events), classify_event=classify_event
                )
            finally:
                sys.path.remove(str(lib))

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


class TestFinalizeTurn:
    def test_renders_the_posted_comment_as_the_deliverable(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(
            turns=[[_finalize_event(_answer(), {"appreciation": "approved"})]]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="raw turn text",
            step_output=STEP_OUTPUT_JSON,
        )

        result = host.run("prompt")

        assert result["deliverable"].startswith("# Wired the format")
        assert "## Changes" in result["deliverable"]
        assert result["answer"] == "raw turn text"
        assert result["turn_outcome"]["appreciation"] == "approved"

    def test_declared_sections_are_extracted_from_the_report_argument(
        self, tmp_path: Path
    ) -> None:
        # An ``output:`` step puts its section markers inside ``report``; the
        # rendered comment is identical to the turn-text channel's.
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(
            turns=[
                [_finalize_event_with_report(_answer(), {"appreciation": "approved"})]
            ]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="raw turn text",
            step_output=STEP_OUTPUT_JSON,
        )

        result = host.run("prompt")

        assert len(conversation.prompts) == 1
        assert result["deliverable"].startswith("# Wired the format")
        assert "## Changes" in result["deliverable"]
        assert result["turn_outcome"]["appreciation"] == "approved"

    def test_restricts_the_tool_surface_to_the_terminal_gate(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        tools = {
            "terminal": object(),
            "finish": object(),
            "dispatch_next": object(),
            "finalize": object(),
            "call_agent": object(),
        }
        conversation = _FinalizeConversation(
            turns=[[_finalize_event(_answer(), {"appreciation": "approved"})]], tools=tools
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="",
            step_output=STEP_OUTPUT_JSON,
        )

        host.run("prompt")

        assert conversation.tools_seen == [{"finalize", "finish"}]
        assert set(conversation.agent._tools) == set(tools)

    def test_retries_once_then_salvages_a_truncated_turn(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(
            turns=[
                [_finalize_event("# headline only\n", {"appreciation": "approved"})],
                [
                    _finalize_event(
                        "# headline\n\n<!-- nebuko:section changes -->\n- a.py\n",
                        {"appreciation": "approved"},
                    )
                ],
            ]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="",
            step_output=STEP_OUTPUT_JSON,
        )

        result = host.run("prompt")

        assert len(conversation.prompts) == 2
        assert "previous attempt failed" in conversation.prompts[1]
        assert result["deliverable"].startswith("# headline")

    def test_repro_995_a_second_malformed_finalize_turn_salvages_instead_of_aborting(
        self, tmp_path: Path
    ) -> None:
        """Reproduces #995: two lead-in-less finalize turns must not abort the run.

        Pre-fix, the handler re-raised the raw ``SectionExtractionError`` on the
        second failure and ``_elicit_finalize`` only caught ``FinalizeToolError``,
        so ``host.run`` raised: ``SectionExtractionError: title field 'headline':
        the '# ' lead-in is missing`` — the ``default-with-output`` step never
        started. The documented contract (one retry, then salvage) now holds.
        """
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        no_lead_in = "## Summary\n\nprose with no required lead-in\n"
        conversation = _FinalizeConversation(
            turns=[
                [_finalize_event(no_lead_in, {"appreciation": "approved"})],
                [_finalize_event(no_lead_in, {"appreciation": "approved"})],
            ]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="",
            step_output=STEP_OUTPUT_JSON,
        )

        result = host.run("prompt")

        assert len(conversation.prompts) == 2
        assert "previous attempt failed" in conversation.prompts[1]
        assert result is not None

    def test_a_step_without_output_still_runs_the_closing_turn(
        self, tmp_path: Path
    ) -> None:
        """#1033: ``output:`` decides extraction only — the turn always runs."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        report = "## Checkpoint\n\n- tests green"
        conversation = _FinalizeConversation(
            turns=[[_finalize_event(report, {"appreciation": "approved"})]]
        )
        host = _host_for(
            repo_root, conversation, latest_answer="legacy", step_output=None
        )

        result = host.run("prompt")

        assert conversation.prompts == [_FINALIZE_PROMPT.format(sections="")]
        assert getattr(conversation, _FINALIZE_BODY_ATTR) == report
        assert result["deliverable"] == report
        assert result["turn_outcome"]["appreciation"] == "approved"

    def test_the_typed_route_reaches_the_routing_decision(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(
            turns=[
                [
                    _finalize_event(
                        _answer(),
                        {
                            "appreciation": "approved",
                            "agent_call": {"agent_name": "review"},
                        },
                    )
                ]
            ]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="",
            step_output=STEP_OUTPUT_JSON,
        )

        result = host.run("prompt")

        assert result["turn_outcome"]["appreciation"] == "approved"

    def test_the_finalize_turn_reads_the_turn_text_and_route(
        self, tmp_path: Path
    ) -> None:
        from nebuko.engine.agent_host import _finalize_route, _finalize_turn_text

        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = [
            _finalize_event(
                _answer(),
                {"appreciation": "hold", "hold_kind": "awaiting_input", "note": "need input"},
            )
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        assert _finalize_turn_text(conversation).startswith("# Wired the format")
        route = _finalize_route(conversation)
        assert route is not None
        assert route.appreciation == "hold"
        assert route.hold_kind == "awaiting_input"
        assert route.note == "need input"


class TestFinalizeTemplatePlaceholder:
    """Regression tests for issue #1020: the engine's template was posted verbatim.

    The runtime event shape that reproduces it: a ``user``-sourced prompt
    ``MessageEvent`` (``send_message``) followed by a ``finalize`` turn whose text
    *is* that prompt — the engine's recall turn replays the finalize prompt onto
    the control turn, so the prompt was harvested as the agent's report, its
    ``# …headline…`` skeleton bodies validated, and the rendered template was
    posted as the comment.
    """

    def _host(
        self,
        tmp_path: Path,
        conversation: object,
        *,
        latest_answer: str = "the real work-loop answer",
    ) -> InProcessAgentHost:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        return _host_for(
            repo_root,
            conversation,
            latest_answer=latest_answer,
            step_output=STEP_OUTPUT_JSON,
            classify_event=None,
        )

    def _prompt(self, contract: OutputContract) -> str:
        from nebuko.engine.agent_host import _FINALIZE_PROMPT

        return _FINALIZE_PROMPT.format(sections=contract.prompt_block())

    def test_the_engine_prompt_is_never_harvested_as_the_report(
        self, tmp_path: Path
    ) -> None:
        """Pre-fix the harvest returned the engine prompt: the template posted.

        Reproduced by the runtime event shape: the control turn replayed the
        engine's own finalize prompt as its text, and no agent-authored message
        stands on the turn.
        """
        from nebuko.engine.agent_host import _finalize_turn_text

        contract = _contract()
        prompt = self._prompt(contract)
        conversation = _FinalizeConversation(turns=[[_engine_prompt_event(prompt)]])
        conversation.state.events.extend(
            [_finalize_event_without_thought({"appreciation": "approved"})]
        )
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        harvested = _finalize_turn_text(conversation)

        assert harvested == ""

        result = self._host(tmp_path, conversation).run("prompt")

        assert result["deliverable"] == "the real work-loop answer"
        assert "…headline…" not in result["deliverable"]

    def test_a_prompt_echoing_finalize_turn_salvages_to_nothing(
        self, tmp_path: Path
    ) -> None:
        """An echoing finalize turn yields no section; the fallback chain runs.

        The turn carries the harness's prompt echo as its text, so no section
        survives extraction or salvage — the run degrades to ``latest_answer.md``
        instead of posting the template.
        """
        from nebuko.engine.agent_host import (
            _REPORT_OFFSET_ATTR,
            _salvage_finalize_body,
        )

        contract = _contract()
        prompt = self._prompt(contract)
        conversation = _FinalizeConversation(turns=[[]])
        conversation.state.events = [
            _agent_message_event(_answer("A true headline")),
            _finalize_event(prompt, {"appreciation": "approved"}),
            _engine_prompt_event(prompt),
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 1)

        body = _salvage_finalize_body(
            FinalizeToolHandler(RunTerminator(contract)), contract, conversation
        )

        assert body == ""
        assert "…headline…" not in body

        result = self._host(tmp_path, conversation).run("prompt")

        # The rendered template is never the posted body. With no section
        # surviving, the run degrades to the captured deliverable — the real
        # agent-authored answer (``capture.deliverable`` prefers the harvested
        # turn text over the raw ``latest_answer.md``), never the placeholder.
        assert result["deliverable"] == _answer("A true headline").strip()
        assert "…headline…" not in result["deliverable"]
        assert "…changes…" not in result["deliverable"]

    def test_each_prompt_body_is_rejected_by_placeholder_validation(self) -> None:
        """The template's bodies are placeholders, so an echo can never validate."""
        contract = _contract()
        renderer = OutputRenderer(contract)
        prompt = self._prompt(contract)
        assert "…headline…" in prompt and "…changes…" in prompt

        for name in ("headline", "changes"):
            with pytest.raises(SectionExtractionError, match="template placeholder"):
                renderer.extract_section(prompt, name)

    def test_an_echoing_prompt_never_renders_a_posted_comment(self) -> None:
        """The reproduced defect: the prompt block rendered the 302-char template."""
        from nebuko.engine.agent_host import _FINALIZE_PROMPT

        contract = _contract()
        prompt = _FINALIZE_PROMPT.format(sections=contract.prompt_block())
        terminator = RunTerminator(
            contract, post=lambda body: None, apply_route=lambda route: None
        )

        with pytest.raises((SectionExtractionError, FinalizeToolError)):
            terminator.finalize(
                TurnOutcomePayload(appreciation="approved"), prompt
            )

    def test_the_engine_prompt_never_reaches_the_harvest_anchor(self) -> None:
        """Every declared field of the prompt is a placeholder, none a body."""
        from nebuko.engine.agent_host import _FINALIZE_PROMPT

        contract = _contract()
        prompt = _FINALIZE_PROMPT.format(sections=contract.prompt_block())
        renderer = OutputRenderer(contract)

        gathered: dict[str, Optional[str]] = {}
        for field in contract.fields:
            try:
                gathered[field.name] = renderer.extract_section(prompt, field.name)
            except SectionExtractionError:
                gathered[field.name] = None

        assert all(body is None for body in gathered.values())
        assert renderer.render({}) == ""

    def test_a_genuine_report_still_wins_over_the_fallback(
        self, tmp_path: Path
    ) -> None:
        """A real turn text is unaffected by the harvest filter."""
        from nebuko.engine.agent_host import _FINALIZE_BODY_ATTR, _finalize_turn_text

        contract = _contract()
        prompt = self._prompt(contract)
        conversation = _FinalizeConversation(
            turns=[
                [
                    _engine_prompt_event(prompt),
                    _agent_message_event(_answer("A true headline")),
                    _finalize_event_without_thought({"appreciation": "approved"}),
                ]
            ]
        )

        result = self._host(tmp_path, conversation).run("prompt")

        expected = OutputRenderer(contract).render(
            {"headline": "A true headline", "changes": "- a.py"}
        )
        assert getattr(conversation, _FINALIZE_BODY_ATTR, None) == expected
        assert result["deliverable"] == expected
        assert _finalize_turn_text(conversation).startswith("# A true headline")


class TestFinalizeHarvestAnchor:
    """The report and its route are anchored to the ``finalize`` event (#1014)."""

    def _conversation(self, events: list[Any]) -> _FinalizeConversation:
        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = events
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)
        return conversation

    def test_a_trailing_message_cannot_clobber_the_report(self) -> None:
        from nebuko.engine.agent_host import _finalize_turn_text

        conversation = self._conversation(
            [
                _finalize_event(_answer(), {"appreciation": "approved"}),
                _agent_message_event("Plan complete and approved for hand-off."),
            ]
        )

        text = _finalize_turn_text(conversation)

        assert text.startswith("# Wired the format")
        assert "hand-off" not in text

    def test_a_preceding_message_is_the_fallback_when_the_thought_is_empty(self) -> None:
        from nebuko.engine.agent_host import _finalize_turn_text

        conversation = self._conversation(
            [
                _agent_message_event(_answer()),
                _finalize_event("", {"appreciation": "approved"}),
                _agent_message_event("Done."),
            ]
        )

        assert _finalize_turn_text(conversation).startswith("# Wired the format")

    def test_a_trailing_message_cannot_decouple_body_and_route(self) -> None:
        from nebuko.engine.agent_host import _finalize_route, _finalize_turn_text

        conversation = self._conversation(
            [
                _finalize_event(
                    _answer(),
                    {
                        "appreciation": "approved",
                        "agent_call": {"agent_name": "review"},
                    },
                ),
                _agent_message_event("just a note"),
            ]
        )

        assert _finalize_turn_text(conversation).startswith("# Wired the format")
        route = _finalize_route(conversation)
        assert route is not None
        assert route.appreciation == "approved"

    def test_events_before_the_offset_are_ignored(self) -> None:
        from nebuko.engine.agent_host import _finalize_route, _finalize_turn_text

        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = [
            _finalize_event(_answer(), {"appreciation": "approved"}),
            _agent_message_event("a later turn"),
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 1)

        # The pre-offset ``finalize`` is out of the harvest window: no anchor,
        # so no report and no route are read from it.
        assert _finalize_turn_text(conversation) == ""
        assert _finalize_route(conversation) is None

    def test_an_empty_harvest_logs_and_falls_back_without_raising(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(turns=[[_agent_message_event("no report")]])
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="work-loop finish note",
            step_output=STEP_OUTPUT_JSON,
        )

        with caplog.at_level("WARNING", logger="nebuko.engine.agent_host"):
            result = host.run("prompt")

        assert getattr(conversation, _FINALIZE_BODY_ATTR, None) is None
        assert result["deliverable"] == "no report"
        assert any("rendered no comment" in record.message for record in caplog.records)

    def test_a_text_less_finalize_turn_is_retried_for_the_report(
        self, tmp_path: Path
    ) -> None:
        # Repro #1038: the closing turn calls ``finalize`` with no report text;
        # the retry turn writes the report, so the plan is posted, not the
        # 134-char work-loop finish note.
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        plan = "## Plan\n\n1. **`a.py`** — the change"
        conversation = _FinalizeConversation(
            turns=[
                [_finalize_event_without_thought({"appreciation": "approved"})],
                [_finalize_event(plan, {"appreciation": "approved"})],
            ]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="work-loop finish note",
            step_output=None,
        )

        result = host.run("prompt")

        assert len(conversation.prompts) == 2
        assert "carried no report" in conversation.prompts[1]
        assert getattr(conversation, _FINALIZE_BODY_ATTR, None) == plan
        assert result["deliverable"] == plan

    def test_a_turn_with_no_text_twice_falls_back_loudly(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(
            turns=[
                [_finalize_event_without_thought({"appreciation": "approved"})],
                [_finalize_event_without_thought({"appreciation": "approved"})],
            ]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="work-loop finish note",
            step_output=None,
        )

        with caplog.at_level("ERROR", logger="nebuko.engine.agent_host"):
            result = host.run("prompt")

        assert len(conversation.prompts) == 2
        assert result["deliverable"] == "work-loop finish note"
        assert any("returned no text twice" in r.message for r in caplog.records)

    def test_a_text_less_finalize_is_refused_and_records_nothing(self) -> None:
        # Repro #1038: an accepted text-less call recorded the outcome, and the
        # agent then acknowledged "Report posted above" with nothing to post.
        from agent_script.features.finalize_tool.finalize_tool import (
            NO_REPORT_TEXT,
            FinalizeExecutor,
        )

        conversation = _FinalizeConversation(turns=[])
        conversation.id = "conv-refused-no-report-text"
        conversation.state.events = [
            _finalize_event_without_thought({"appreciation": "approved"})
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)
        action = conversation.state.events[0].action

        observation = FinalizeExecutor(conversation_id=conversation.id)(
            action, conversation
        )

        assert observation.is_error
        assert observation.text == NO_REPORT_TEXT
        assert _recorded_turn_outcome_fields(conversation) is None

    def test_outside_the_closing_turn_an_outcome_only_finalize_is_accepted(self) -> None:
        # The dispatch recall pass asks for the outcome alone ("do not restate
        # your answer"); only the engine's closing turn owes a report.
        from agent_script.features.finalize_tool.finalize_tool import FinalizeExecutor

        conversation = _FinalizeConversation(turns=[])
        conversation.id = "conv-recall-outcome-only"
        conversation.state.events = [
            _finalize_event_without_thought({"appreciation": "approved"})
        ]
        action = conversation.state.events[0].action

        observation = FinalizeExecutor(conversation_id=conversation.id)(
            action, conversation
        )

        assert not observation.is_error
        assert _recorded_turn_outcome_fields(conversation) is not None

    def test_the_report_argument_is_the_report(self) -> None:
        from nebuko.engine.report_text import closing_turn_report_text

        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = [
            _finalize_event_with_report("## Plan\n\nthe change", {"appreciation": "approved"})
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        assert closing_turn_report_text(conversation) == "## Plan\n\nthe change"

    def test_a_rejected_call_still_yields_its_report(self) -> None:
        # The call omitted the required ``turn_outcome``, so the SDK recorded it
        # with ``action=None``; the report it carried must not be lost.
        from nebuko.engine.report_text import closing_turn_report_text

        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = [
            _rejected_finalize_event({"report": "## Plan\n\nthe change"})
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        assert closing_turn_report_text(conversation) == "## Plan\n\nthe change"

    @pytest.mark.parametrize("raw", ['{"report": "cut off', "[]", '{"report": 3}'])
    def test_unusable_raw_arguments_yield_no_report(self, raw: str) -> None:
        from nebuko.engine.report_text import closing_turn_report_text

        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = [_rejected_finalize_event(raw)]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        assert closing_turn_report_text(conversation) == ""

    def test_a_rejected_call_with_a_report_is_posted_without_a_retry(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        plan = "## Plan\n\n1. **`a.py`** - the change"
        conversation = _FinalizeConversation(
            turns=[[_rejected_finalize_event({"report": plan})]]
        )
        host = _host_for(
            repo_root, conversation, latest_answer="work-loop finish note", step_output=None
        )

        result = host.run("prompt")

        assert len(conversation.prompts) == 1
        assert result["deliverable"] == plan

    def test_the_observation_answering_the_call_is_never_the_anchor(self) -> None:
        # Repro #1038 (CI run 39317188689): the SDK emits the call's observation
        # with the same ``tool_name``; anchoring on it read no report from a call
        # that carried one, three times in a row.
        from nebuko.engine.report_text import (
            closing_turn_report_text,
            finalize_anchor_index,
        )

        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = _with_observations(
            [
                _finalize_event_with_report("## Plan\n\nthe change", {"appreciation": "approved"}),
                _agent_message_event("Report posted above."),
            ]
        )
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        assert finalize_anchor_index(conversation) == 0
        assert closing_turn_report_text(conversation) == "## Plan\n\nthe change"

    def test_a_report_in_the_turn_text_survives_the_observation(self) -> None:
        from nebuko.engine.report_text import closing_turn_report_text

        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = _with_observations(
            [_finalize_event("## Plan\n\nthe change", {"appreciation": "approved"})]
        )
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        assert closing_turn_report_text(conversation) == "## Plan\n\nthe change"

    def test_the_report_argument_wins_over_the_turn_text(self) -> None:
        from nebuko.engine.report_text import closing_turn_report_text

        event = _finalize_event(
            "lead-in text", {"appreciation": "approved"}, report="the report"
        )
        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = [event]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        assert closing_turn_report_text(conversation) == "the report"

    def test_a_finalize_call_with_a_report_argument_is_posted_without_a_retry(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        plan = "## Plan\n\n1. **`a.py`** - the change"
        conversation = _FinalizeConversation(
            turns=[[_finalize_event_with_report(plan, {"appreciation": "approved"})]]
        )
        host = _host_for(
            repo_root, conversation, latest_answer="work-loop finish note", step_output=None
        )

        result = host.run("prompt")

        assert len(conversation.prompts) == 1
        assert "`report` argument" in conversation.prompts[0]
        assert result["deliverable"] == plan

    def test_a_blank_report_argument_is_refused_in_the_closing_turn(self) -> None:
        from agent_script.features.finalize_tool.finalize_tool import (
            NO_REPORT_TEXT,
            FinalizeExecutor,
        )

        conversation = _FinalizeConversation(turns=[])
        conversation.id = "conv-blank-report-argument"
        conversation.state.events = [
            _finalize_event_with_report("   ", {"appreciation": "approved"})
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        observation = FinalizeExecutor(conversation_id=conversation.id)(
            conversation.state.events[0].action, conversation
        )

        assert observation.is_error
        assert observation.text == NO_REPORT_TEXT

    def test_a_report_written_before_finalize_is_accepted(self) -> None:
        from agent_script.features.finalize_tool.finalize_tool import FinalizeExecutor

        conversation = _FinalizeConversation(turns=[])
        conversation.id = "conv-accepted-with-report-text"
        conversation.state.events = [
            _agent_message_event(_answer()),
            _finalize_event_without_thought({"appreciation": "approved"}),
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)
        action = conversation.state.events[-1].action

        observation = FinalizeExecutor(conversation_id=conversation.id)(
            action, conversation
        )

        assert not observation.is_error
        assert _recorded_turn_outcome_fields(conversation) is not None

    def test_an_ack_after_a_refused_finalize_never_becomes_the_report(
        self, tmp_path: Path
    ) -> None:
        # Repro #1038: the agent acks the refusal instead of writing a report.
        # Neither attempt has report text, so the loud fallback stands; the ack
        # is never the posted deliverable.
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(
            turns=[
                [
                    _finalize_event_without_thought({"appreciation": "approved"}),
                    _agent_message_event("Report posted above."),
                ],
                [
                    _finalize_event_without_thought({"appreciation": "approved"}),
                    _agent_message_event("Report posted above again."),
                ],
            ]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="work-loop finish note",
            step_output=None,
        )

        result = host.run("prompt")

        assert getattr(conversation, _FINALIZE_BODY_ATTR, None) is None
        assert "Report posted above" not in result["deliverable"]

    def test_a_broken_turn_salvages_the_text_seen_so_far(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)

        class _BrokenConversation(_FinalizeConversation):
            def send_message(self, prompt: str) -> None:
                # The turn emits the completed sections, then the send/run seam
                # breaks before the harvest would normally run.
                self.prompts.append(prompt)
                self.state.events.extend(
                    [_finalize_event(_answer(), {"appreciation": "approved"})]
                )
                raise RuntimeError("send broke after emitting the report")

        conversation = _BrokenConversation(turns=[])
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="work-loop finish note",
            step_output=STEP_OUTPUT_JSON,
        )

        result = host.run("prompt")

        assert result["deliverable"].startswith("# Wired the format")
        assert "## Changes" in result["deliverable"]


def _recorded_turn_outcome_fields(conversation: Any) -> dict[str, Any] | None:
    """The ``turn_outcome`` the closing ``finalize`` tool recorded, as state JSON.

    Mirrors the real tool executor, which records the outcome in the run-scoped
    registry that ``build_output`` reads (issue #1033).
    """
    from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
        get_recorded_turn_outcome,
    )

    conv_id = getattr(conversation, "id", None)
    if conv_id is None:
        return None
    outcome = get_recorded_turn_outcome(conv_id)
    if outcome is None:
        return None
    fields: dict[str, Any] = {
        "status": outcome.status.value if outcome.status else "",
        "appreciation": outcome.appreciation.value if outcome.appreciation else "",
        "hold_kind": outcome.hold_kind.value if outcome.hold_kind else "",
        "note": outcome.note or "",
    }
    return {key: value for key, value in fields.items() if value}
