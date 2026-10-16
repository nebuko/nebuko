"""Tests for the FunctionCall model and its typed hand-off extractor."""

import pytest

from workflow_state import FunctionCall, extract_function_call


class TestFunctionCallModel:
    def test_defaults(self):
        fc = FunctionCall(agent_name="context")
        assert fc.function == "call_agent"
        # "" (unset) lets the resolver apply the called step's declared tier
        # before defaulting to "medium" (issue #787).
        assert fc.complexity == ""
        assert fc.reason is None

    def test_unknown_function_rejected(self):
        with pytest.raises(ValueError, match="explode"):
            FunctionCall.model_validate({"function": "explode", "agent_name": "context"})

    def test_full_payload(self):
        fc = FunctionCall.model_validate(
            {
                "function": "call_agent",
                "agent_name": "dev",
                "complexity": "high",
                "reason": "needs implementation",
            }
        )
        assert fc.agent_name == "dev"
        assert fc.complexity == "high"
        assert fc.reason == "needs implementation"

    def test_round_trip_preserves_all_fields(self):
        fc = FunctionCall.model_validate(
            {"function": "call_agent", "agent_name": "review", "complexity": "low"}
        )
        assert FunctionCall.model_validate(fc.model_dump()) == fc


class TestExtractFunctionCall:
    def test_sibling_section_shape(self):
        data = {
            "dispatch": {"status": "advance"},
            "function_call": {
                "function": "call_agent",
                "agent_name": "context",
                "complexity": "medium",
            },
        }
        fc = extract_function_call(data)
        assert fc is not None and fc.agent_name == "context"

    def test_absent_returns_none(self):
        assert extract_function_call({"dispatch": {"status": "advance"}}) is None
        assert extract_function_call(None) is None
        assert extract_function_call("not-a-dict") is None

    def test_invalid_payload_returns_none_without_raising(self):
        # Invalid function calls must not corrupt the dispatch path.
        data = {"function_call": {"function": "explode", "agent_name": "context"}}
        assert extract_function_call(data) is None
        assert extract_function_call({"function_call": "garbage"}) is None
