"""Unit tests for the engine-tool control-plane registry (issue #830).

The registry is the declarative contract between the engine and every agent:
``finish`` -> the final message, ``dispatch_next`` -> the routing decision.
These tests drive the structural typed-field read with plain duck-typed actions
and with a real SDK ``FinishAction``, so the classification matches production.
"""

from __future__ import annotations

from types import SimpleNamespace

from nebuko.engine.engine_tools import DEFAULT_ENGINE_TOOLS, EngineToolRegistry


class TestDefaultRegistry:
    def test_finish_is_classified_as_the_final_message(self):
        spec = DEFAULT_ENGINE_TOOLS.tools["finish"]
        assert spec.payload_kind == "final_message"
        assert spec.payload_fields == ("message",)

    def test_dispatch_next_carries_the_turn_outcome_fields(self):
        spec = DEFAULT_ENGINE_TOOLS.tools["dispatch_next"]
        assert spec.payload_kind == "routing"
        assert spec.payload_fields == (
            "appreciation",
            "hold_kind",
            "required_agent",
            "round",
            "note",
            "complexity",
            "pin",
        )

    def test_finish_is_the_work_loop_terminator(self):
        assert DEFAULT_ENGINE_TOOLS.work_loop_presence("finish") == "terminator"

    def test_dispatch_next_is_hidden_in_the_work_loop(self):
        assert DEFAULT_ENGINE_TOOLS.work_loop_presence("dispatch_next") == "hidden"

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

    def test_harvests_the_turn_outcome_fields(self):
        action = SimpleNamespace(
            appreciation="approved",
            hold_kind="",
            complexity="high",
            note="done",
            required_agent="review",
            pin=True,
        )
        payload = DEFAULT_ENGINE_TOOLS.harvest("dispatch_next", action)
        assert payload is not None
        assert payload.payload_kind == "routing"
        assert payload.fields == {
            "appreciation": "approved",
            "hold_kind": "",
            "required_agent": "review",
            "round": "",
            "note": "done",
            "complexity": "high",
            "pin": "true",
        }

    def test_missing_optional_fields_become_empty_strings(self):
        action = SimpleNamespace(appreciation="hold")
        payload = DEFAULT_ENGINE_TOOLS.harvest("dispatch_next", action)
        assert payload.fields == {
            "appreciation": "hold",
            "hold_kind": "",
            "required_agent": "",
            "round": "",
            "note": "",
            "complexity": "",
            "pin": "",
        }

    def test_an_unset_boolean_flag_is_empty_not_false(self):
        # ``pin=False`` must read as "unset" so it never masks a fallback value.
        action = SimpleNamespace(appreciation="approved", pin=False)
        payload = DEFAULT_ENGINE_TOOLS.harvest("dispatch_next", action)
        assert payload.fields["pin"] == ""

    def test_none_field_is_an_empty_string(self):
        action = SimpleNamespace(appreciation="approved", hold_kind=None, note=None)
        payload = DEFAULT_ENGINE_TOOLS.harvest("dispatch_next", action)
        assert payload.fields["hold_kind"] == ""
        assert payload.fields["note"] == ""

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
