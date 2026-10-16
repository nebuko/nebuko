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
from typing import Any

import pytest

from nebuko.engine.agent_host import (
    _FINALIZE_BODY_ATTR,
    _REPORT_OFFSET_ATTR,
    _REPORT_PAYLOAD_ATTR,
    _REPORT_PROMPT,
    InProcessAgentHost,
)
from nebuko.engine.engine_tools import (
    DEFAULT_ENGINE_TOOLS,
    FinalizeToolError,
    FinalizeToolHandler,
)
from nebuko.engine.finalize import (
    OutputContract,
    RouteDecision,
    RunTerminator,
    step_output_contract,
)

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
        assert spec.payload_fields == ("route",)
        assert DEFAULT_ENGINE_TOOLS.work_loop_presence("finalize") == "terminator"

    def test_finalize_tool_exposes_the_route_only_schema(self) -> None:
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c1")[0]
        schema = tool.response_schema
        assert isinstance(schema, dict)
        assert list(schema["properties"]) == ["route"]

    def test_finalize_action_records_the_typed_route(self) -> None:
        from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
            get_recorded_dispatch,
        )
        from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

        tool = FinalizeTool.create(conversation_id="c-route")[0]
        action = tool.action_from_arguments(
            {"route": {"status": "advance", "agent": "review", "pin": True}}
        )
        tool.executor(action)

        dispatch = get_recorded_dispatch("c-route")
        assert dispatch is not None
        assert dispatch.status == "advance"
        assert dispatch.task_hint == "review"
        assert dispatch.pin is True


class TestFinalizeToolHandler:
    def _handler(self) -> tuple[FinalizeToolHandler, list[str]]:
        posted: list[str] = []
        terminator = RunTerminator(_contract(), post=posted.append)
        return FinalizeToolHandler(terminator), posted

    def test_renders_the_posted_comment_on_a_valid_answer(self) -> None:
        handler, posted = self._handler()

        report = handler.handle(RouteDecision(status="advance"), _answer())

        assert report.title == "Wired the format"
        assert report.sections["changes"] == "- a.py"
        assert posted and posted[0].startswith("# Wired the format")
        assert "## Changes" in posted[0]

    def test_first_failure_is_retryable_and_names_the_field(self) -> None:
        handler, posted = self._handler()

        with pytest.raises(FinalizeToolError) as excinfo:
            handler.handle(RouteDecision(), "# headline only\n")

        assert excinfo.value.retryable is True
        assert excinfo.value.field == "changes"
        assert posted == []

    def test_second_failure_fails_loudly(self) -> None:
        handler, _ = self._handler()
        with pytest.raises(FinalizeToolError):
            handler.handle(RouteDecision(), "# headline only\n")

        with pytest.raises(Exception) as excinfo:
            handler.handle(RouteDecision(), "# headline only\n")

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


def _finalize_event(text: str, route: dict[str, Any] | None = None) -> object:
    """A real ``finalize`` action event carrying the turn's report text."""
    from agent_script.features.finalize_tool.finalize_tool import FinalizeAction
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    action = FinalizeAction()
    if route is not None:
        action._structured_output = {"route": route}
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
        self.state = SimpleNamespace(events=[])
        self.agent = _FakeAgent(tools or {})
        self._turns = list(turns)
        self.prompts: list[str] = []
        self.tools_seen: list[set[str]] = []

    def send_message(self, prompt: str) -> None:
        self.prompts.append(prompt)

    def run(self) -> None:
        self.tools_seen.append(set(self.agent._tools))
        if self._turns:
            self.state.events.extend(self._turns.pop(0))

    def interrupt(self) -> None:  # pragma: no cover - the timeout path
        pass


def _host_for(
    repo_root: Path,
    conversation: object,
    *,
    latest_answer: str,
    step_output: str | None,
    state_dispatch: dict[str, Any] | None = None,
) -> InProcessAgentHost:
    def _run_agent(llm_config, prompt, args, agent_cfg=None, workspace=None):
        return conversation

    def _build_output(conversation, metrics, args, prev_state=None):
        Path("latest_answer.md").write_text(latest_answer, encoding="utf-8")
        Path("final_message.txt").write_text("finish note", encoding="utf-8")
        state: dict[str, Any] = {
            "version": 1,
            "workflow": "w",
            "step": "s",
            "agent": "dev",
        }
        if state_dispatch is not None:
            state["dispatch"] = state_dispatch
        Path("workflow_state.json").write_text(json.dumps(state), encoding="utf-8")

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
            turns=[[_finalize_event(_answer(), {"status": "advance"})]]
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
        assert getattr(conversation, _REPORT_PAYLOAD_ATTR, None) is None

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
            turns=[[_finalize_event(_answer(), {"status": "advance"})]], tools=tools
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="",
            step_output=STEP_OUTPUT_JSON,
        )

        host.run("prompt")

        assert conversation.tools_seen == [{"finalize", "call_agent"}]
        assert set(conversation.agent._tools) == set(tools)

    def test_retries_once_then_salvages_a_truncated_turn(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(
            turns=[
                [_finalize_event("# headline only\n", {"status": "advance"})],
                [
                    _finalize_event(
                        "# headline\n\n<!-- nebuko:section changes -->\n- a.py\n",
                        {"status": "advance"},
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

    def test_a_step_without_output_keeps_the_report_turn(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(turns=[[]])
        host = _host_for(
            repo_root, conversation, latest_answer="legacy", step_output=None
        )

        result = host.run("prompt")

        assert conversation.prompts == [_REPORT_PROMPT]
        assert getattr(conversation, _FINALIZE_BODY_ATTR, None) is None
        assert result["deliverable"] == "legacy"

    def test_the_typed_route_reaches_the_routing_decision(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FinalizeConversation(
            turns=[
                [
                    _finalize_event(
                        _answer(), {"status": "advance", "agent": "review", "pin": True}
                    )
                ]
            ]
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="",
            step_output=STEP_OUTPUT_JSON,
            state_dispatch={
                "status": "advance",
                "task_hint": "review",
                "complexity": "",
                "branch": None,
                "pin": True,
                "reason": None,
            },
        )

        result = host.run("prompt")

        assert result["dispatch"]["status"] == "advance"
        assert result["dispatch"]["agent"] == "review"

    def test_the_finalize_turn_reads_the_turn_text_and_route(
        self, tmp_path: Path
    ) -> None:
        from nebuko.engine.agent_host import _finalize_route, _finalize_turn_text

        conversation = _FinalizeConversation(turns=[])
        conversation.state.events = [
            _finalize_event(_answer(), {"status": "hold", "reason": "need input"})
        ]
        setattr(conversation, _REPORT_OFFSET_ATTR, 0)

        assert _finalize_turn_text(conversation).startswith("# Wired the format")
        route = _finalize_route(conversation)
        assert route is not None
        assert route.status == "hold"
        assert route.reason == "need input"
