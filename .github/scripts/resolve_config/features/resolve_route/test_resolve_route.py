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


class TestResolveFunctionCallPathWorkflowContext:
    """issue #507/#506: function_call inside an active workflow must carry
    the workflow context and establish the callee's actual step."""

    @staticmethod
    def _workflows_dir(tmp_path, yaml_text):
        (tmp_path / "spec.yaml").write_text(yaml_text)
        return str(tmp_path)

    SPEC_YAML = (
        "name: spec\n"
        "description: test\n"
        "trigger:\n"
        "  agents: [genius]\n"
        "steps:\n"
        "  - name: analyze\n"
        "    agent: genius\n"
        "    next: context\n"
        "  - name: context\n"
        "    agent: ctx\n"
        "    next: plan\n"
        "  - name: plan\n"
        "    agent: genius\n"
        "    next: null\n"
    )

    def test_issue506_repro_callee_step_established(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall, WorkflowState

        # genius (step=analyze) hands off to ctx via function_call while
        # workflow 'spec' is active — the exact #506 trigger shape.
        prev = WorkflowState(workflow="spec", step="analyze", agent="genius")
        fc = FunctionCall(agent_name="ctx")
        config = _resolve_function_call_path(fc, self._workflows_dir(tmp_path, self.SPEC_YAML), prev)
        assert config["task"] == "ctx"
        # The defect this fixes: empty values made ctx post the stale
        # caller step, dead-ending the next re-trigger on the loop guard.
        assert config["config_workflow"] == "spec"
        assert config["config_step"] == "context"

    def test_callee_not_linear_successor_falls_back_to_owned_step(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall, WorkflowState

        # genius at plan calls dev directly; the linear successor of plan
        # belongs to ctx, so the helper falls back to the first step owned
        # by dev (build).
        yaml = (
            "name: spec\n"
            "description: test\n"
            "trigger:\n"
            "  agents: [genius]\n"
            "steps:\n"
            "  - name: plan\n"
            "    agent: genius\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: ctx\n"
            "    next: build\n"
            "  - name: build\n"
            "    agent: dev\n"
            "    next: null\n"
        )
        d = self._workflows_dir(tmp_path, yaml)
        prev = WorkflowState(workflow="spec", step="plan", agent="genius")
        config = _resolve_function_call_path(FunctionCall(agent_name="dev"), d, prev)
        assert config["config_step"] == "build"

    def test_callee_owns_no_step_keeps_empty(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="spec", step="analyze", agent="genius")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="summarize"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["task"] == "summarize"
        assert config["config_workflow"] == "spec"
        assert config["config_step"] == ""

    def test_unknown_workflow_keeps_empty(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="ghost", step="analyze", agent="genius")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="ctx"), str(tmp_path), prev
        )
        assert config["config_workflow"] == "ghost"
        assert config["config_step"] == ""

    def test_no_prev_state_keeps_legacy_empty(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall

        config = _resolve_function_call_path(
            FunctionCall(agent_name="ctx"), self._workflows_dir(tmp_path, self.SPEC_YAML)
        )
        assert config["config_workflow"] == ""
        assert config["config_step"] == ""


class TestWorkflowCommandPrecedence:
    """Tests for /workflow <name> multi-directory resolution (issue #529)."""

    def _wf_yaml(self, agent):
        return (
            "name: foo\n"
            'description: "test"\n'
            "trigger:\n"
            f"  agents: [{agent}]\n"
            "steps:\n"
            f"  - name: only\n    agent: {agent}\n    next: null\n"
        )

    def _dir(self, tmp_path: Path, subdir: str, contents: dict) -> Path:
        d = tmp_path / subdir
        d.mkdir()
        for fname, content in contents.items():
            (d / fname).write_text(content)
        return d

    def test_local_override_wins_over_builtin(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("dev")})
        local = self._dir(tmp_path, "local", {"foo.yaml": self._wf_yaml("ctx")})
        config = _resolve_human_path(
            "/workflow foo", None, None, str(base), local_workflows_dir=str(local)
        )
        assert config["task"] == "ctx"
        assert config["config_workflow"] == "foo"
        assert "SKIP_AGENT" not in config

    def test_builtin_used_when_local_absent(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("genius")})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/workflow foo", None, None, str(base), local_workflows_dir=str(local)
        )
        assert config["task"] == "genius"
        assert config["config_workflow"] == "foo"

    def test_missing_everywhere_falls_back_to_mention_with_skip_reason(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/workflow nope", None, None, str(base), local_workflows_dir=str(local)
        )
        assert config["task"] == ""
        assert config["config_workflow"] == ""
        # Enriched diagnostic carried for the terminal skip comment
        reason = config["config_skip_reason"]
        assert "nope" in reason
        assert str(base / "nope.yaml") in reason
        assert str(local / "nope.yaml") in reason

    def test_schema_invalid_local_override_fails_loudly(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("dev")})
        local = self._dir(tmp_path, "local", {"foo.yaml": "name: foo\n"})
        config = _resolve_human_path(
            "/workflow foo", None, None, str(base), local_workflows_dir=str(local)
        )
        # No silent fallthrough to the built-in of the same name
        assert config["task"] == ""
        assert config["config_workflow"] == ""
        assert "schema-invalid" in config["config_skip_reason"]

    def test_valid_resolution_has_no_skip_reason(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("dev")})
        config = _resolve_human_path("/workflow foo", None, None, str(base))
        assert config["task"] == "dev"
        assert "config_skip_reason" not in config
