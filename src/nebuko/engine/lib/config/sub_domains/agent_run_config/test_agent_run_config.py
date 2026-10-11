"""Tests for the canonical AgentRunConfig sentinel contract (issue #765).

Covers the two invariants shared by the validator and the router — at least
one action, and ``task`` XOR ``function_call`` — plus the ``callback`` return
address on the existing ``FunctionCall`` model and the no-action default.
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config.sub_domains.agent_run_config.agent_run_config import (
    AgentRunConfig,
    DispatchConfig,
)
from workflow_state.sub_domains.function_call.function_call import FunctionCall


class TestAgentRunConfigContract:
    def test_task_only_valid(self):
        """Legacy workflow-relative selection still validates."""
        config = AgentRunConfig.model_validate({"task": "dev"})
        assert config.task == "dev"
        assert config.dispatch is None
        assert config.function_call is None

    def test_dispatch_only_valid(self):
        """Defect D1 regression: the dispatch-only sentinel context emits is valid."""
        config = AgentRunConfig.model_validate(
            {"dispatch": {"status": "advance"}}
        )
        assert config.dispatch is not None
        assert config.dispatch.status == "advance"
        assert config.task is None

    def test_function_call_only_valid(self):
        config = AgentRunConfig.model_validate(
            {"function_call": {"function": "call_agent", "agent_name": "context"}}
        )
        assert config.function_call is not None
        assert config.function_call.agent_name == "context"

    def test_empty_payload_rejected(self):
        with pytest.raises(ValidationError, match="at least one"):
            AgentRunConfig.model_validate({})

    def test_unrelated_field_only_rejected(self):
        with pytest.raises(ValidationError, match="at least one"):
            AgentRunConfig.model_validate({"complexity": "high"})

    def test_task_combined_with_function_call_rejected(self):
        with pytest.raises(ValidationError, match="cannot combine"):
            AgentRunConfig.model_validate(
                {
                    "task": "architect",
                    "function_call": {
                        "function": "call_agent",
                        "agent_name": "context",
                    },
                }
            )

    def test_task_and_dispatch_allowed(self):
        config = AgentRunConfig.model_validate(
            {"task": "dev", "dispatch": {"status": "advance"}}
        )
        assert config.task == "dev"
        assert config.dispatch.status == "advance"


class TestDispatchConfig:
    def test_no_action_default_is_hold(self):
        default = DispatchConfig.no_action_default("no-action default: test")
        assert default.status == "hold"
        assert default.reason == "no-action default: test"
        assert default.origin == "engine_fallback"

    def test_origin_defaults_to_agent(self):
        config = DispatchConfig(status="advance")
        assert config.origin == "agent"

    def test_fallback_for_without_callback_holds(self):
        """Rudderless run (no return address) holds for a human (#765)."""
        fallback = DispatchConfig.fallback_for(callback="")
        assert fallback.status == "hold"
        assert fallback.origin == "engine_fallback"
        assert "no-action default" in fallback.reason
        # Never names an explicit destination, so the callback gate can fire.
        assert fallback.workflow is None and fallback.step is None

    def test_fallback_for_with_callback_advances_to_caller(self):
        """Decision-less run with a return address routes back (#864)."""
        fallback = DispatchConfig.fallback_for(callback="architect")
        assert fallback.status == "advance"
        assert fallback.origin == "engine_fallback"
        assert "architect" in fallback.reason
        assert fallback.step is None and fallback.workflow is None

    def test_fallback_for_carries_diagnostic_in_reason(self):
        fallback = DispatchConfig.fallback_for(
            callback="architect", diagnostic="truncated"
        )
        assert fallback.status == "advance"
        assert "truncated" in fallback.reason
        assert "architect" in fallback.reason

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            DispatchConfig(status="teleport")

    def test_invalid_origin_rejected(self):
        with pytest.raises(ValidationError):
            DispatchConfig(status="hold", origin="guess")


class TestFunctionCallCallback:
    def test_callback_defaults_to_none(self):
        call = FunctionCall(agent_name="context")
        assert call.callback is None

    def test_callback_round_trips(self):
        call = FunctionCall(agent_name="context", callback="architect")
        assert call.callback == "architect"
        assert FunctionCall.model_validate(call.model_dump()) == call
