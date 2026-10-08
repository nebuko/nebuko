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

    def test_unset_complexity_defers_to_step_tier(self):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall

        # An unset caller tier stays "" here so build_result can apply the
        # callee step's declared tier before defaulting to "medium" (#787).
        # Defaulting to "medium" at this layer would mask the step tier.
        config = _resolve_function_call_path(FunctionCall(agent_name="dev"), "/tmp")
        assert config["config_complexity"] == ""

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
            FunctionCall(agent_name="debug"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["task"] == "debug"
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
    """Tests for /nebuko run workflow --name <name> multi-directory resolution (issue #529)."""

    @staticmethod
    def _run_cmd(name):
        from resolve_config.sub_domains.workflow_command.workflow_command import (
            parse_workflow_command,
        )
        return parse_workflow_command("/nebuko run workflow --name " + name)

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
            "/nebuko run workflow --name foo", None, None, str(base), local_workflows_dir=str(local), workflow_command=self._run_cmd("foo")
        )
        assert config["task"] == "ctx"
        assert config["config_workflow"] == "foo"
        assert "SKIP_AGENT" not in config

    def test_builtin_used_when_local_absent(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("genius")})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/nebuko run workflow --name foo", None, None, str(base), local_workflows_dir=str(local), workflow_command=self._run_cmd("foo")
        )
        assert config["task"] == "genius"
        assert config["config_workflow"] == "foo"

    def test_missing_everywhere_falls_back_to_mention_with_skip_reason(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/nebuko run workflow --name nope", None, None, str(base), local_workflows_dir=str(local), workflow_command=self._run_cmd("nope")
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
            "/nebuko run workflow --name foo", None, None, str(base), local_workflows_dir=str(local), workflow_command=self._run_cmd("foo")
        )
        # No silent fallthrough to the built-in of the same name
        assert config["task"] == ""
        assert config["config_workflow"] == ""
        assert "schema-invalid" in config["config_skip_reason"]

    def test_valid_resolution_has_no_skip_reason(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("dev")})
        config = _resolve_human_path("/nebuko run workflow --name foo", None, None, str(base), workflow_command=self._run_cmd("foo"))
        assert config["task"] == "dev"
        assert "config_skip_reason" not in config

    # --- issue #789: /nebuko run workflow --step <step> ---

    _MULTI_STEP_YAML = (
        "name: foo\n"
        'description: "test"\n'
        "trigger:\n"
        "  agents: [genius]\n"
        "steps:\n"
        "  - name: one\n    agent: genius\n    next: two\n"
        "  - name: two\n    agent: dev\n    next: null\n"
    )

    def _step_cmd(self, step):
        from resolve_config.sub_domains.workflow_command.workflow_command import (
            parse_workflow_command,
        )
        return parse_workflow_command(
            "/nebuko run workflow --name foo --step " + step
        )

    def test_run_step_enters_at_named_step(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._MULTI_STEP_YAML})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/nebuko run workflow --name foo --step two", None, None, str(base),
            local_workflows_dir=str(local), workflow_command=self._step_cmd("two"),
        )
        assert config["task"] == "dev"
        assert config["config_workflow"] == "foo"
        assert config["config_step"] == "two"
        assert "SKIP_AGENT" not in config

    def test_run_without_step_keeps_first_step(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._MULTI_STEP_YAML})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/nebuko run workflow --name foo", None, None, str(base),
            local_workflows_dir=str(local), workflow_command=self._run_cmd("foo"),
        )
        assert config["task"] == "genius"
        assert config["config_workflow"] == "foo"
        assert config["config_step"] == ""

    def test_run_step_unknown_step_fails_loudly(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._MULTI_STEP_YAML})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/nebuko run workflow --name foo --step nope", None, None, str(base),
            local_workflows_dir=str(local), workflow_command=self._step_cmd("nope"),
        )
        assert config["task"] == ""
        assert config["config_workflow"] == ""
        assert "not found in workflow 'foo'" in config["config_skip_reason"]

    def test_run_step_path_traversal_rejected(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._MULTI_STEP_YAML})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/nebuko run workflow --name foo --step ../etc/passwd", None, None,
            str(base), local_workflows_dir=str(local),
            workflow_command=self._step_cmd("../etc/passwd"),
        )
        assert config["task"] == ""
        assert "invalid step name" in config["config_skip_reason"]


# ---------------------------------------------------------------------------
# Object Design Contract for issue #733: explicit routing precedence
# (gate > function_call; forward-only cross-agent hand-off).
#
# Stub step (stub-only): these behaviour-pinning spec tests are RED against the
# current resolver. They turn GREEN when the `implement` step makes
# `_resolve_callee_step` forward-only and rewires the cascade at
# `resolve_config.resolve_config` to consult the gate before `function_call`.
# No production routing behaviour changes in this step.
# ---------------------------------------------------------------------------

_GATED_HANDOFF_WF_YAML = (
    "name: spec-driven-impl\n"
    "description: test\n"
    "trigger:\n"
    "  agents: [genius]\n"
    "steps:\n"
    "  - name: stub\n"
    "    agent: dev\n"
    "    next: stub_review\n"
    "  - name: stub_review\n"
    "    agent: review\n"
    "    next: stub_approval\n"
    "  - name: stub_approval\n"
    "    agent: genius\n"
    "    human_gate: true\n"
    "    next: implement\n"
    "  - name: implement\n"
    "    agent: dev\n"
    "    next: review\n"
    "  - name: review\n"
    "    agent: review\n"
    "    next: null\n"
)


class TestHandoffDecisionContract:
    """The frozen `HandoffDecision` value object (issue #733 Object Design)."""

    def test_carries_the_resolved_target_and_reason(self):
        from resolve_config.features.resolve_route.resolve_route import HandoffDecision

        decision = HandoffDecision(
            target_agent="genius", target_step="stub_approval", reason_code="gate_hold"
        )
        assert (decision.target_agent, decision.target_step, decision.reason_code) == (
            "genius",
            "stub_approval",
            "gate_hold",
        )

    def test_to_config_maps_task_and_step(self):
        from resolve_config.features.resolve_route.resolve_route import HandoffDecision

        decision = HandoffDecision(
            target_agent="dev", target_step="implement", reason_code="function_call"
        )
        assert decision.to_config() == {"task": "dev", "step": "implement"}


class TestForwardOnlyCalleeResolution:
    """A cross-agent hand-off may only target a *forward* step (issue #733)."""

    def _workflows_dir(self, tmp_path):
        (tmp_path / "spec-driven-impl.yaml").write_text(_GATED_HANDOFF_WF_YAML)
        return str(tmp_path)

    def test_never_re_enters_an_already_consumed_step(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_callee_step

        resolved = _resolve_callee_step(
            "spec-driven-impl", "stub_review", "dev", self._workflows_dir(tmp_path)
        )
        # `stub` sits *before* the current position and is already consumed.
        assert resolved != "stub"
        assert resolved == "implement"


class TestGatePrecedenceOverFunctionCall:
    """A human gate outranks an agent `function_call` (issue #733)."""

    @staticmethod
    def _dirs(tmp_path):
        from resolve_workflow import TASK_TO_PROMPT

        prompt_base = tmp_path / "prompts"
        workflows_dir = tmp_path / "workflows"
        prompt_base.mkdir()
        workflows_dir.mkdir()
        for name in TASK_TO_PROMPT.values():
            (prompt_base / name).write_text("# prompt")
        (workflows_dir / "spec-driven-impl.yaml").write_text(_GATED_HANDOFF_WF_YAML)
        return str(prompt_base), str(workflows_dir)

    def _resolve(self, tmp_path, state):
        import json as _json

        from resolve_config import resolve_config

        block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        prompt_base, workflows_dir = self._dirs(tmp_path)
        body = "<!-- bot-workflow-terminal -->\n" + block
        return resolve_config(body, prompt_base, workflows_dir, is_bot=True)

    def test_function_call_across_a_gate_resolves_to_the_gate(self, tmp_path):
        # `stub_review` (review) approved and carries a hand-off to `dev`; its
        # workflow successor is `stub_approval` (genius, human_gate). The gate
        # must win — the call must not jump it to a consumed `stub` step.
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "spec-driven-impl",
                "step": "stub_review",
                "agent": "review",
                "dispatch": {"status": "advance", "task_hint": "dev"},
                "function_call": {"agent_name": "dev"},
            },
        )
        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "genius"
        assert result["WORKFLOW_STEP"] == "stub_approval"

    def test_function_call_without_a_forward_step_is_a_no_op(self, tmp_path):
        # A hand-off from the last step has no forward callee step; it must not
        # re-enter a consumed step (the loop this contract kills).
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "spec-driven-impl",
                "step": "review",
                "agent": "review",
                "dispatch": {"status": "advance", "task_hint": "dev"},
                "function_call": {"agent_name": "dev"},
            },
        )
        assert result.get("WORKFLOW_STEP") != "stub"

    def test_no_forward_step_without_dispatch_continues_workflow(self, tmp_path):
        # A no-op hand-off (callee owns no forward step) with **no** dispatch
        # object must fall through to `_resolve_workflow_next_step`, continuing
        # the workflow from the current step rather than re-entering a
        # consumed one.
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "spec-driven-impl",
                "step": "implement",
                "agent": "dev",
                "function_call": {"agent_name": "ctx"},
            },
        )
        assert result["TASK"] == "review"
        assert result["WORKFLOW_STEP"] == "review"

    def _resolve_real_workflow(self, tmp_path, state):
        import json as _json

        from resolve_config import resolve_config
        from resolve_workflow import TASK_TO_PROMPT

        prompt_base = tmp_path / "prompts"
        prompt_base.mkdir()
        for name in TASK_TO_PROMPT.values():
            (prompt_base / name).write_text("# prompt")
        workflows_dir = str(Path(__file__).resolve().parents[5] / "workflows")
        block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        body = "<!-- bot-workflow-terminal -->\n" + block
        return resolve_config(body, str(prompt_base), workflows_dir, is_bot=True)

    def test_reported_loop_rerouted_to_gate_on_the_real_workflow(self, tmp_path):
        # End-to-end repro of the reported #733 loop against the shipped
        # workflows/spec-driven-impl.yaml (not the synthetic copy above):
        # review@stub_review approved and handed off to `dev`. Forward-only
        # callee resolution plus gate precedence must land on the human gate,
        # never back on the consumed `dev@stub` step.
        result = self._resolve_real_workflow(
            tmp_path,
            {
                "version": 1,
                "workflow": "spec-driven-impl",
                "step": "stub_review",
                "agent": "review",
                "dispatch": {"status": "advance", "task_hint": "dev"},
                "function_call": {"agent_name": "dev"},
            },
        )
        assert result["TASK"] == "genius"
        assert result["WORKFLOW_STEP"] == "stub_approval"


_CALLBACK_WF_YAML = (
    "name: cb\n"
    "description: test\n"
    "trigger:\n"
    "  agents: [genius]\n"
    "steps:\n"
    "  - name: context\n"
    "    agent: ctx\n"
    "    next: build\n"
    "  - name: build\n"
    "    agent: dev\n"
    "    next: null\n"
    "  - name: answer\n"
    "    agent: genius\n"
    "    next: null\n"
)


class TestCallbackReturnTarget:
    """Unit tests for the callback return-address resolver (issue #765)."""

    @staticmethod
    def _state(tmp_path, callback):
        from workflow_state import WorkflowState

        (tmp_path / "cb.yaml").write_text(_CALLBACK_WF_YAML)
        return WorkflowState(
            workflow="cb", step="context", agent="ctx", callback=callback
        ), str(tmp_path)

    def test_advance_with_no_target_returns_callback_step(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _callback_return_target

        state, wf_dir = self._state(tmp_path, "genius")
        assert _callback_return_target(state, None, wf_dir) == ("genius", "answer")

    def test_no_callback_returns_none(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _callback_return_target

        state, wf_dir = self._state(tmp_path, None)
        assert _callback_return_target(state, None, wf_dir) is None

    def test_callback_equal_to_self_is_noop(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _callback_return_target

        state, wf_dir = self._state(tmp_path, "ctx")
        assert _callback_return_target(state, None, wf_dir) is None

    def test_explicit_target_outranks_callback(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _callback_return_target
        from workflow_state import Dispatch

        state, wf_dir = self._state(tmp_path, "genius")
        dispatch = Dispatch(status="advance", task_hint="dev")
        assert _callback_return_target(state, dispatch, wf_dir) is None

    def test_hold_outranks_callback(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _callback_return_target
        from workflow_state import Dispatch

        state, wf_dir = self._state(tmp_path, "genius")
        assert _callback_return_target(state, Dispatch(status="hold"), wf_dir) is None

    def test_callback_absent_from_workflow_falls_through(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _callback_return_target

        # `review` owns no step in this workflow: never emit a bare callback
        # into a workflow; let the forward edge handle it instead.
        state, wf_dir = self._state(tmp_path, "review")
        assert _callback_return_target(state, None, wf_dir) is None


class TestCallbackReturnAddressEndToEnd:
    """End-to-end callback routing through resolve_config (issue #765)."""

    @staticmethod
    def _dirs(tmp_path):
        from resolve_workflow import TASK_TO_PROMPT

        prompt_base = tmp_path / "prompts"
        workflows_dir = tmp_path / "workflows"
        prompt_base.mkdir()
        workflows_dir.mkdir()
        for name in TASK_TO_PROMPT.values():
            (prompt_base / name).write_text("# prompt")
        (workflows_dir / "cb.yaml").write_text(_CALLBACK_WF_YAML)
        return str(prompt_base), str(workflows_dir)

    def _resolve(self, tmp_path, state):
        import json as _json

        from resolve_config import resolve_config

        block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        prompt_base, workflows_dir = self._dirs(tmp_path)
        body = "<!-- bot-workflow-terminal -->\n" + block
        return resolve_config(body, prompt_base, workflows_dir, is_bot=True)

    def test_callee_advance_routes_back_to_caller_not_forward_edge(self, tmp_path):
        # ctx was reached via genius's function_call (callback=genius) and ends
        # with a bare advance. The workflow forward edge is ctx->dev(build);
        # the callback must win and route back to genius(answer).
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "cb",
                "step": "context",
                "agent": "ctx",
                "callback": "genius",
                "dispatch": {"status": "advance"},
            },
        )
        assert result["TASK"] == "genius"
        assert result["WORKFLOW_STEP"] == "answer"

    def test_callee_without_callback_continues_workflow_edge(self, tmp_path):
        # Same run but no callback registered: unchanged behavior - follow the
        # workflow forward edge to dev.
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "cb",
                "step": "context",
                "agent": "ctx",
                "dispatch": {"status": "advance"},
            },
        )
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_STEP"] == "build"


class TestFunctionCallPathCallbackDefault:
    """The function_call resolver records the callback return address (#765)."""

    @staticmethod
    def _workflows_dir(tmp_path, yaml_text):
        (tmp_path / "spec.yaml").write_text(yaml_text)
        return str(tmp_path)

    SPEC_YAML = TestResolveFunctionCallPathWorkflowContext.SPEC_YAML

    def test_callback_defaults_to_emitting_caller(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="spec", step="analyze", agent="genius")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="ctx"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["config_callback"] == "genius"

    def test_explicit_callback_overrides_default(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="spec", step="analyze", agent="genius")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="ctx", callback="issue"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["config_callback"] == "issue"

    def test_callback_equal_to_callee_is_cleared(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="spec", step="analyze", agent="genius")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="genius"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["config_callback"] == ""

    def test_no_prev_state_has_no_callback(self, tmp_path):
        from resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from workflow_state import FunctionCall

        config = _resolve_function_call_path(
            FunctionCall(agent_name="ctx"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
        )
        assert config["config_callback"] == ""

