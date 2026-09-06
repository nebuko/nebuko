"""Tests for resolve_route feature (routing policy helpers)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.features.resolve_route.resolve_route import (  # noqa: E402
    MAX_LOOP_COUNT,
    count_consecutive_same_agent,
)


class TestCountConsecutiveSameAgent:
    def test_counts_trailing_run(self):
        from workflow_state import HistoryEntry
        history = [
            HistoryEntry(step="s1", agent="genius"),
            HistoryEntry(step="s2", agent="dev"),
            HistoryEntry(step="s3", agent="dev"),
            HistoryEntry(step="s4", agent="dev"),
        ]
        assert count_consecutive_same_agent(history, "dev") == 3

    def test_no_run(self):
        from workflow_state import HistoryEntry
        history = [HistoryEntry(step="s1", agent="genius")]
        assert count_consecutive_same_agent(history, "dev") == 0

    def test_empty_history(self):
        assert count_consecutive_same_agent([], "dev") == 0

    def test_max_loop_count_safeguard(self):
        assert MAX_LOOP_COUNT == 5


class TestResolveFunctionCallPath:
    def test_pins_target_agent_and_complexity(self):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall

        fc = FunctionCall.model_validate(
            {"agent_name": "ctx", "complexity": "high"}
        )
        config = _resolve_function_call_path(fc, "/tmp/workflows")
        assert config["task"] == "ctx"
        assert config["config_complexity"] == "high"
        # Hand-off runs outside any workflow context
        assert config["config_workflow"] == ""
        assert config["config_step"] == ""
        assert "SKIP_AGENT" not in config

    def test_default_complexity_medium(self):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall

        config = _resolve_function_call_path(FunctionCall(agent_name="dev"), "/tmp")
        assert config["config_complexity"] == "medium"

    def test_missing_agent_name_rejected_by_model(self):
        from workflow_state import FunctionCall

        # The model validator (added by issue #478 review) rejects blank
        # agent names before they can reach the resolver.
        with pytest.raises(ValueError, match="non-empty agent_name"):
            FunctionCall(agent_name="")
        with pytest.raises(ValueError, match="non-empty agent_name"):
            FunctionCall(agent_name="   ")

    def test_missing_agent_name_skips_via_model_construct(self):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall

        # Bypass model validation to exercise the resolver's own guard.
        fc = FunctionCall.model_construct(agent_name="")
        config = _resolve_function_call_path(fc, "/tmp")
        assert config["SKIP_AGENT"] == "true"
        assert "agent_name" in config["REASON"]

    def test_unknown_function_skips(self):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall

        # Bypass registry validation to simulate drift
        fc = FunctionCall.model_construct(function="explode", agent_name="ctx")
        config = _resolve_function_call_path(fc, "/tmp")
        assert config["SKIP_AGENT"] == "true"
        assert "explode" in config["REASON"]
