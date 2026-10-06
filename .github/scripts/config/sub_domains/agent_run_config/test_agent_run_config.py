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
        """Defect D1 regression: the dispatch-only sentinel ctx emits is valid."""
        config = AgentRunConfig.model_validate(
            {"dispatch": {"status": "advance"}}
        )
        assert config.dispatch is not None
        assert config.dispatch.status == "advance"
        assert config.task is None

    def test_function_call_only_valid(self):
        config = AgentRunConfig.model_validate(
            {"function_call": {"function": "call_agent", "agent_name": "ctx"}}
        )
        assert config.function_call is not None
        assert config.function_call.agent_name == "ctx"

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
                    "task": "genius",
                    "function_call": {
                        "function": "call_agent",
                        "agent_name": "ctx",
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

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            DispatchConfig(status="teleport")


class TestFunctionCallCallback:
    def test_callback_defaults_to_none(self):
        call = FunctionCall(agent_name="ctx")
        assert call.callback is None

    def test_callback_round_trips(self):
        call = FunctionCall(agent_name="ctx", callback="genius")
        assert call.callback == "genius"
        assert FunctionCall.model_validate(call.model_dump()) == call
