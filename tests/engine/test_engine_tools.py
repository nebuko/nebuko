"""Unit tests for the engine-tool control-plane registry (issues #830, #1033).

The registry is the declarative contract between the engine and every agent:
``finish`` -> the final message, ``finalize`` -> the typed turn outcome.
These tests drive the structural typed-field read with plain duck-typed actions
and with a real SDK ``FinishAction``, so the classification matches production.
"""

from __future__ import annotations

from types import SimpleNamespace

from nebuko.engine.engine_tools import DEFAULT_ENGINE_TOOLS, EngineToolRegistry


class TestDefaultRegistry:
    def test_finalize_is_the_only_terminal_tool(self):
        # Issue #1071: ``finish`` is retired from the control plane; the legacy
        # spec in _LEGACY_TOOL_SPECS keeps recorded pre-migration runs readable.
        spec = DEFAULT_ENGINE_TOOLS.tools["finalize"]
        assert spec.payload_kind == "finalize"
        assert spec.payload_fields == ("turn_outcome",)
        assert "finish" not in DEFAULT_ENGINE_TOOLS.tools

    def test_finish_is_a_legacy_read_only_spec(self):
        from nebuko.engine.engine_tools import _LEGACY_TOOL_SPECS

        spec = _LEGACY_TOOL_SPECS["finish"]
        assert spec.payload_kind == "final_message"
        assert spec.payload_fields == ("message",)
        assert DEFAULT_ENGINE_TOOLS.work_loop_presence("finish") == "hidden"

    def test_the_removed_tools_are_not_in_the_default_control_plane(self):
        # Issue #1033: ``dispatch_next``/``call_agent`` are gone from every
        # agent's tool list; ``finalize`` is the one terminal tool.
        for name in ("dispatch_next", "call_agent"):
            assert name not in DEFAULT_ENGINE_TOOLS.tools
        assert DEFAULT_ENGINE_TOOLS.work_loop_presence("finalize") == "terminator"

    def test_unknown_tool_is_hidden(self):
        assert DEFAULT_ENGINE_TOOLS.work_loop_presence("terminal") == "hidden"


class TestHarvest:
    def test_harvests_the_finish_message(self):
        action = SimpleNamespace(message="  Full report  ")
        payload = DEFAULT_ENGINE_TOOLS.harvest("finish", action)
        assert payload is not None
        assert payload.tool_name == "finish"
        assert payload.payload_kind == "final_message"
        assert payload.fields == {"message": "Full report"}

    def test_reads_the_turn_outcome_argument(self):
        action = SimpleNamespace(
            structured_output={
                "turn_outcome": {
                    "appreciation": "approved",
                    "complexity": "high",
                    "note": "done",
                }
            }
        )
        payload = DEFAULT_ENGINE_TOOLS.finalize_payload(action)
        assert payload == {
            "appreciation": "approved",
            "complexity": "high",
            "note": "done",
        }

    def test_reads_a_declared_turn_outcome_field(self):
        action = SimpleNamespace(
            turn_outcome=SimpleNamespace(
                appreciation="hold", hold_kind="awaiting_input"
            )
        )
        payload = DEFAULT_ENGINE_TOOLS.finalize_payload(action)
        assert payload == {"appreciation": "hold", "hold_kind": "awaiting_input"}

    def test_an_action_without_a_turn_outcome_is_empty(self):
        assert DEFAULT_ENGINE_TOOLS.finalize_payload(SimpleNamespace()) == {}
        assert DEFAULT_ENGINE_TOOLS.finalize_payload(None) == {}

    def test_unknown_tool_returns_none(self):
        assert DEFAULT_ENGINE_TOOLS.harvest("terminal", SimpleNamespace(message="x")) is None

    def test_missing_action_returns_none(self):
        assert DEFAULT_ENGINE_TOOLS.harvest("finish", None) is None

    def test_harvests_a_real_sdk_finish_action(self):
        from openhands.sdk.tool.builtins.finish import FinishAction

        payload = DEFAULT_ENGINE_TOOLS.harvest("finish", FinishAction(message="Report body"))
        assert payload.fields == {"message": "Report body"}


class TestRegistryInjection:
    def test_a_custom_registry_supplies_a_different_control_plane(self):
        from nebuko.engine.engine_tools import EngineToolSpec

        registry = EngineToolRegistry(
            tools={
                "summarize": EngineToolSpec(
                    name="summarize",
                    payload_kind="final_message",
                    payload_fields=("summary",),
                )
            }
        )
        payload = registry.harvest("summarize", SimpleNamespace(summary="brief"))
        assert payload.fields == {"summary": "brief"}
        assert registry.work_loop_presence("summarize") == "terminator"
        # The default control plane is untouched by the injected one.
        assert DEFAULT_ENGINE_TOOLS.harvest("summarize", SimpleNamespace(summary="x")) is None
