"""Tests for resolve_route feature (routing policy helpers)."""

import sys
from pathlib import Path

import pytest

from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import (
    MAX_LOOP_COUNT,
    count_consecutive_same_agent,
)


def _entry(step: str, agent: str):
    """A history entry with the mandatory factual outcome (issue #964)."""
    from nebuko.engine.lib.workflow_state import Appreciation, HistoryEntry, TurnOutcome, TurnStatus

    return HistoryEntry(
        step=step,
        agent=agent,
        turn_outcome=TurnOutcome(
            status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED
        ),
    )


class TestCountConsecutiveSameAgent:
    def test_counts_trailing_run(self):
        from nebuko.engine.lib.workflow_state import HistoryEntry
        history = [
            _entry("s1", "architect"),
            _entry("s2", "dev"),
            _entry("s3", "dev"),
            _entry("s4", "dev"),
        ]
        assert count_consecutive_same_agent(history, "dev") == 3

    def test_no_run(self):
        from nebuko.engine.lib.workflow_state import HistoryEntry
        history = [_entry("s1", "architect")]
        assert count_consecutive_same_agent(history, "dev") == 0

    def test_empty_history(self):
        assert count_consecutive_same_agent([], "dev") == 0

    def test_max_loop_count_safeguard(self):
        assert MAX_LOOP_COUNT == 5


class TestResolveFunctionCallPath:
    def test_pins_target_agent_and_complexity(self):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall

        fc = FunctionCall.model_validate(
            {"agent_name": "context", "complexity": "high"}
        )
        config = _resolve_function_call_path(fc, "/tmp/workflows")
        assert config["task"] == "context"
        assert config["config_complexity"] == "high"
        # Hand-off runs outside any workflow context
        assert config["config_workflow"] == ""
        assert config["config_step"] == ""
        assert "SKIP_AGENT" not in config

    def test_unset_complexity_defers_to_step_tier(self):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall

        # An unset caller tier stays "" here so build_result can apply the
        # callee step's declared tier before defaulting to "medium" (#787).
        # Defaulting to "medium" at this layer would mask the step tier.
        config = _resolve_function_call_path(FunctionCall(agent_name="dev"), "/tmp")
        assert config["config_complexity"] == ""

    def test_missing_agent_name_rejected_by_model(self):
        from nebuko.engine.lib.workflow_state import FunctionCall

        # The model validator (added by issue #478 review) rejects blank
        # agent names before they can reach the resolver.
        with pytest.raises(ValueError, match="non-empty agent_name"):
            FunctionCall(agent_name="")
        with pytest.raises(ValueError, match="non-empty agent_name"):
            FunctionCall(agent_name="   ")

    def test_missing_agent_name_skips_via_model_construct(self):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall

        # Bypass model validation to exercise the resolver's own guard.
        fc = FunctionCall.model_construct(agent_name="")
        config = _resolve_function_call_path(fc, "/tmp")
        assert config["SKIP_AGENT"] == "true"
        assert "agent_name" in config["REASON"]

    def test_unknown_function_skips(self):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall

        # Bypass registry validation to simulate drift
        fc = FunctionCall.model_construct(function="explode", agent_name="context")
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
        "steps:\n"
        "  - name: analyze\n"
        "    agent: architect\n"
        "    next: context\n"
        "  - name: context\n"
        "    agent: context\n"
        "    next: plan\n"
        "  - name: plan\n"
        "    agent: architect\n"
        "    next: null\n"
    )

    def test_issue506_repro_callee_step_established(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall, WorkflowState

        # architect (step=analyze) hands off to context via function_call while
        # workflow 'spec' is active — the exact #506 trigger shape.
        prev = WorkflowState(workflow="spec", step="analyze", agent="architect")
        fc = FunctionCall(agent_name="context")
        config = _resolve_function_call_path(fc, self._workflows_dir(tmp_path, self.SPEC_YAML), prev)
        assert config["task"] == "context"
        # The defect this fixes: empty values made context post the stale
        # caller step, dead-ending the next re-trigger on the loop guard.
        assert config["config_workflow"] == "spec"
        assert config["config_step"] == "context"

    def test_callee_not_linear_successor_falls_back_to_owned_step(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall, WorkflowState

        # architect at plan calls dev directly; the linear successor of plan
        # belongs to context, so the helper falls back to the first step owned
        # by dev (build).
        yaml = (
            "name: spec\n"
            "description: test\n"
            "steps:\n"
            "  - name: plan\n"
            "    agent: architect\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: context\n"
            "    next: build\n"
            "  - name: build\n"
            "    agent: dev\n"
            "    next: null\n"
        )
        d = self._workflows_dir(tmp_path, yaml)
        prev = WorkflowState(workflow="spec", step="plan", agent="architect")
        config = _resolve_function_call_path(FunctionCall(agent_name="dev"), d, prev)
        assert config["config_step"] == "build"

    def test_callee_owns_no_step_keeps_empty(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="spec", step="analyze", agent="architect")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="debug"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["task"] == "debug"
        assert config["config_workflow"] == "spec"
        assert config["config_step"] == ""

    def test_unknown_workflow_keeps_empty(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="ghost", step="analyze", agent="architect")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="context"), str(tmp_path), prev
        )
        assert config["config_workflow"] == "ghost"
        assert config["config_step"] == ""

    def test_no_prev_state_keeps_legacy_empty(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall

        config = _resolve_function_call_path(
            FunctionCall(agent_name="context"), self._workflows_dir(tmp_path, self.SPEC_YAML)
        )
        assert config["config_workflow"] == ""
        assert config["config_step"] == ""


class TestWorkflowCommandPrecedence:
    """Tests for /nebuko run workflow --name <name> multi-directory resolution (issue #529)."""

    @staticmethod
    def _run_cmd(name):
        from nebuko.engine.lib.resolve_config.sub_domains.workflow_command.workflow_command import (
            parse_workflow_command,
        )
        return parse_workflow_command("/nebuko run workflow --name " + name)

    def _wf_yaml(self, agent):
        return (
            "name: foo\n"
            'description: "test"\n'
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
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("dev")})
        local = self._dir(tmp_path, "local", {"foo.yaml": self._wf_yaml("context")})
        config = _resolve_human_path(
            "/nebuko run workflow --name foo", None, None, str(base), local_workflows_dir=str(local), workflow_command=self._run_cmd("foo")
        )
        assert config["task"] == "context"
        assert config["config_workflow"] == "foo"
        assert "SKIP_AGENT" not in config

    def test_builtin_used_when_local_absent(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("architect")})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/nebuko run workflow --name foo", None, None, str(base), local_workflows_dir=str(local), workflow_command=self._run_cmd("foo")
        )
        assert config["task"] == "architect"
        assert config["config_workflow"] == "foo"

    def test_missing_everywhere_falls_back_to_mention_with_skip_reason(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

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
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

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
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._wf_yaml("dev")})
        config = _resolve_human_path("/nebuko run workflow --name foo", None, None, str(base), workflow_command=self._run_cmd("foo"))
        assert config["task"] == "dev"
        assert "config_skip_reason" not in config

    # --- issue #789: /nebuko run workflow --step <step> ---

    _MULTI_STEP_YAML = (
        "name: foo\n"
        'description: "test"\n'
        "steps:\n"
        "  - name: one\n    agent: architect\n    next: two\n"
        "  - name: two\n    agent: dev\n    next: null\n"
    )

    def _step_cmd(self, step):
        from nebuko.engine.lib.resolve_config.sub_domains.workflow_command.workflow_command import (
            parse_workflow_command,
        )
        return parse_workflow_command(
            "/nebuko run workflow --name foo --step " + step
        )

    def test_run_step_enters_at_named_step(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

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
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

        base = self._dir(tmp_path, "base", {"foo.yaml": self._MULTI_STEP_YAML})
        local = self._dir(tmp_path, "local", {})
        config = _resolve_human_path(
            "/nebuko run workflow --name foo", None, None, str(base),
            local_workflows_dir=str(local), workflow_command=self._run_cmd("foo"),
        )
        assert config["task"] == "architect"
        assert config["config_workflow"] == "foo"
        assert config["config_step"] == ""

    def test_run_step_unknown_step_fails_loudly(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

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
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path

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
    "steps:\n"
    "  - name: stub\n"
    "    agent: dev\n"
    "    next: stub_review\n"
    "  - name: stub_review\n"
    "    agent: review\n"
    "    next: stub_approval\n"
    "  - name: stub_approval\n"
    "    agent: architect\n"
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
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import HandoffDecision

        decision = HandoffDecision(
            target_agent="architect", target_step="stub_approval", reason_code="gate_hold"
        )
        assert (decision.target_agent, decision.target_step, decision.reason_code) == (
            "architect",
            "stub_approval",
            "gate_hold",
        )

    def test_to_config_maps_task_and_step(self):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import HandoffDecision

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
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_callee_step

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
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

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

        from nebuko.engine.lib.resolve_config import resolve_config

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
        # workflow successor is `stub_approval` (architect, human_gate). The gate
        # must win — the call must not jump it to a consumed `stub` step.
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "spec-driven-impl",
                "step": "stub_review",
                "agent": "review",
                "turn_outcome": {
                    "status": "completed",
                    "appreciation": "approved",
                    "required_agent": "dev",
                },
                "function_call": {"agent_name": "dev"},
            },
        )
        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "architect"
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
                "turn_outcome": {
                    "status": "completed",
                    "appreciation": "approved",
                    "required_agent": "dev",
                },
                "function_call": {"agent_name": "dev"},
            },
        )
        assert result.get("WORKFLOW_STEP") != "stub"

    def test_no_forward_step_without_outcome_continues_workflow(self, tmp_path):
        # A no-op hand-off (callee owns no forward step) with **no** recorded
        # outcome must fall through to `_resolve_workflow_next_step`, continuing
        # the workflow from the current step rather than re-entering a
        # consumed one.
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "spec-driven-impl",
                "step": "implement",
                "agent": "dev",
                "function_call": {"agent_name": "context"},
            },
        )
        assert result["TASK"] == "review"
        assert result["WORKFLOW_STEP"] == "review"

    def _resolve_real_workflow(self, tmp_path, state):
        import json as _json

        from nebuko.engine.lib.resolve_config import resolve_config
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

        prompt_base = tmp_path / "prompts"
        prompt_base.mkdir()
        for name in TASK_TO_PROMPT.values():
            (prompt_base / name).write_text("# prompt")
        # The shipped definitions, not the per-repo `.nebuko` override:
        # `config_dir` prefers a checkout-local override directory whose
        # contents vary per machine, so pin the packaged defaults.
        from nebuko.engine.lib.default_paths import checkout_root
        workflows_dir = str(checkout_root() / "defaults" / "workflows")
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
                "turn_outcome": {
                    "status": "completed",
                    "appreciation": "approved",
                    "required_agent": "dev",
                },
                "function_call": {"agent_name": "dev"},
            },
        )
        assert result["TASK"] == "architect"
        assert result["WORKFLOW_STEP"] == "stub_approval"


_GATED_HOLD_WF_YAML = (
    "name: gated\n"
    "description: test\n"
    "trigger:\n"
    "  agents: [architect]\n"
    "steps:\n"
    "  - name: plan\n"
    "    agent: architect\n"
    "    next: implement\n"
    "    human_gate: true\n"
    "  - name: implement\n"
    "    agent: dev\n"
    "    next: review\n"
    "  - name: review\n"
    "    agent: review\n"
    "    next: null\n"
)


class TestHumanGateHoldPrecedence:
    """A persisted ``human_gate`` hold blocks auto-advance (issues #885, #887).

    The exact inversion this fix closes: before the guard, a ``hold`` whose
    reason named a gate was ignored whenever the workflow forward edge
    resolved (``plan -> implement``), so the bot advanced to ``dev``.
    """

    @staticmethod
    def _dirs(tmp_path):
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

        prompt_base = tmp_path / "prompts"
        workflows_dir = tmp_path / "workflows"
        prompt_base.mkdir()
        workflows_dir.mkdir()
        for name in TASK_TO_PROMPT.values():
            (prompt_base / name).write_text("# prompt")
        (workflows_dir / "gated.yaml").write_text(_GATED_HOLD_WF_YAML)
        return str(prompt_base), str(workflows_dir)

    def _resolve(self, tmp_path, state):
        import json as _json

        from nebuko.engine.lib.resolve_config import resolve_config

        block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        prompt_base, workflows_dir = self._dirs(tmp_path)
        body = "<!-- bot-workflow-terminal -->\n" + block
        return resolve_config(body, prompt_base, workflows_dir, is_bot=True)

    def _state(self, **outcome):
        return {
            "version": 1,
            "workflow": "gated",
            "step": "plan",
            "agent": "architect",
            "turn_outcome": outcome,
        }

    def test_gate_hold_outranks_a_resolvable_successor(self, tmp_path):
        # plan -> implement resolves, but the armed gate must still win. The
        # agent itself says 'approved' (issue #1033): the gate is run state.
        result = self._resolve(
            tmp_path,
            {
                **self._state(status="completed", appreciation="approved"),
                "gate": {"step": "plan", "status": "armed"},
            },
        )
        assert result["SKIP_AGENT"] == "true"
        assert "human_gate:plan" in result["REASON"]
        assert "approve gate" in result["REASON"]
        assert "reject gate" in result["REASON"]

    def test_gate_hold_result_never_names_the_successor(self, tmp_path):
        result = self._resolve(
            tmp_path,
            self._state(
                status="completed",
                appreciation="hold",
                hold_kind="awaiting_approval",
            ),
        )
        assert result.get("TASK") != "dev"
        assert result.get("WORKFLOW_STEP") != "implement"

    def test_plain_hold_parks_the_step(self, tmp_path):
        # A hold verdict is a deliberate stop (issue #964): it parks the step
        # for the human even when the workflow forward edge would resolve.
        result = self._resolve(
            tmp_path,
            self._state(
                status="completed",
                appreciation="hold",
                hold_kind="awaiting_input",
            ),
        )
        assert result["SKIP_AGENT"] == "true"
        assert "Awaiting input" in result["REASON"]
        assert result.get("TASK") != "dev"

    def test_gate_blocks_the_advance_but_never_the_handoff(self, tmp_path):
        # Issue #1003: the gate blocks the workflow *advance*, not a
        # delegation — a hand-off from an armed gate is a consultation and the
        # step position stays put.
        state = {
            **self._state(status="completed", appreciation="approved"),
            "gate": {"step": "plan", "status": "armed"},
        }
        state["function_call"] = {"agent_name": "dev"}
        result = self._resolve(tmp_path, state)
        assert result.get("SKIP_AGENT") != "true"
        assert result.get("TASK") == "dev"
        # The callee runs at its own workflow step; the gate re-arms on return.
        assert result.get("WORKFLOW_STEP") == "implement"


_CALLBACK_WF_YAML = (
    "name: cb\n"
    "description: test\n"
    "steps:\n"
    "  - name: context\n"
    "    agent: context\n"
    "    next: build\n"
    "  - name: build\n"
    "    agent: dev\n"
    "    next: null\n"
    "  - name: answer\n"
    "    agent: architect\n"
    "    next: null\n"
)


class TestCallbackReturnTarget:
    """Unit tests for the callback return-address resolver (issue #765)."""

    @staticmethod
    def _state(tmp_path, callback):
        from nebuko.engine.lib.workflow_state import WorkflowState

        (tmp_path / "cb.yaml").write_text(_CALLBACK_WF_YAML)
        return WorkflowState(
            workflow="cb", step="context", agent="context", callback=callback
        ), str(tmp_path)

    def test_completion_with_no_target_returns_callback_step(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _callback_return_target

        state, wf_dir = self._state(tmp_path, "architect")
        assert _callback_return_target(state, None, wf_dir) == ("architect", "answer")

    def test_no_callback_returns_none(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _callback_return_target

        state, wf_dir = self._state(tmp_path, None)
        assert _callback_return_target(state, None, wf_dir) is None

    def test_callback_equal_to_self_is_noop(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _callback_return_target

        state, wf_dir = self._state(tmp_path, "context")
        assert _callback_return_target(state, None, wf_dir) is None

    def test_approved_verdict_fires_the_callback(self, tmp_path):
        """Issue #1033: ``approved``/``rejected`` both return to the caller."""
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _callback_return_target
        from nebuko.engine.lib.workflow_state import Appreciation, TurnOutcome, TurnStatus

        state, wf_dir = self._state(tmp_path, "architect")
        for verdict in (Appreciation.APPROVED, Appreciation.REJECTED):
            outcome = TurnOutcome(status=TurnStatus.COMPLETED, appreciation=verdict)
            assert _callback_return_target(state, outcome, wf_dir) == (
                "architect",
                "answer",
            )

    def test_hold_outranks_callback(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _callback_return_target
        from nebuko.engine.lib.workflow_state import Appreciation, HoldKind, TurnOutcome, TurnStatus

        state, wf_dir = self._state(tmp_path, "architect")
        hold = TurnOutcome(
            status=TurnStatus.COMPLETED,
            appreciation=Appreciation.HOLD,
            hold_kind=HoldKind.AWAITING_INPUT,
        )
        assert _callback_return_target(state, hold, wf_dir) is None

    def test_callback_absent_from_workflow_falls_through(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _callback_return_target

        # `review` owns no step in this workflow: never emit a bare callback
        # into a workflow; let the forward edge handle it instead.
        state, wf_dir = self._state(tmp_path, "review")
        assert _callback_return_target(state, None, wf_dir) is None

    def test_engine_fallback_outcome_passes_the_gate(self, tmp_path):
        """The #864 synthesized fallback (advance, no destination) returns home.

        A decision-less callee with a return address now emits an
        ``INCOMPLETE``/``NO_OUTCOME`` outcome carrying no destination fields, so
        the existing callback gate carries it to the caller.
        """
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _callback_return_target
        from nebuko.engine.lib.workflow_state import IncompleteReason, TurnOutcome

        state, wf_dir = self._state(tmp_path, "architect")
        outcome = TurnOutcome.for_incomplete(
            IncompleteReason.NO_OUTCOME,
            note="no-action default: agent emitted no decision; returning to caller 'architect'",
        )
        assert _callback_return_target(state, outcome, wf_dir) == ("architect", "answer")

    def test_agent_explicit_hold_still_outranks_callback(self, tmp_path):
        """An explicit agent hold is never redirected to the callback (#864)."""
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _callback_return_target
        from nebuko.engine.lib.workflow_state import Appreciation, HoldKind, TurnOutcome, TurnStatus

        state, wf_dir = self._state(tmp_path, "architect")
        hold = TurnOutcome(
            status=TurnStatus.COMPLETED,
            appreciation=Appreciation.HOLD,
            hold_kind=HoldKind.AWAITING_INPUT,
        )
        assert _callback_return_target(state, hold, wf_dir) is None


class TestCallbackReturnAddressEndToEnd:
    """End-to-end callback routing through resolve_config (issue #765)."""

    @staticmethod
    def _dirs(tmp_path):
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

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

        from nebuko.engine.lib.resolve_config import resolve_config

        block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        prompt_base, workflows_dir = self._dirs(tmp_path)
        body = "<!-- bot-workflow-terminal -->\n" + block
        return resolve_config(body, prompt_base, workflows_dir, is_bot=True)

    def test_callee_approval_routes_back_to_caller_not_forward_edge(self, tmp_path):
        # context was reached via architect's function_call (callback=architect) and ends
        # with a bare approval. The workflow forward edge is context->dev(build);
        # the callback must win and route back to architect(answer).
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "cb",
                "step": "context",
                "agent": "context",
                "callback": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
            },
        )
        assert result["TASK"] == "architect"
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
                "agent": "context",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
            },
        )
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_STEP"] == "build"

    def test_engine_fallback_outcome_returns_to_caller(self, tmp_path):
        """The #864 fix end-to-end: a decision-less callee returns home.

        The state carries the synthesized fallback (``INCOMPLETE`` /
        ``NO_OUTCOME`` with no destination) that #864 makes the engine emit
        instead of a bare ``hold``; with ``callback: architect`` the run must
        resolve back to the caller, not the forward edge.
        """
        result = self._resolve(
            tmp_path,
            {
                "version": 1,
                "workflow": "cb",
                "step": "context",
                "agent": "context",
                "callback": "architect",
                "turn_outcome": {
                    "status": "incomplete",
                    "incomplete_reason": "no_outcome",
                    "note": "no-action default: agent emitted no decision; "
                    "returning to caller 'architect'",
                },
            },
        )
        assert result["TASK"] == "architect"
        assert result["WORKFLOW_STEP"] == "answer"


class TestFunctionCallPathCallbackDefault:
    """The function_call resolver records the callback return address (#765)."""

    @staticmethod
    def _workflows_dir(tmp_path, yaml_text):
        (tmp_path / "spec.yaml").write_text(yaml_text)
        return str(tmp_path)

    SPEC_YAML = TestResolveFunctionCallPathWorkflowContext.SPEC_YAML

    def test_callback_defaults_to_emitting_caller(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="spec", step="analyze", agent="architect")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="context"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["config_callback"] == "architect"

    def test_explicit_callback_overrides_default(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="spec", step="analyze", agent="architect")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="context", callback="issue"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["config_callback"] == "issue"

    def test_callback_equal_to_callee_is_cleared(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall, WorkflowState

        prev = WorkflowState(workflow="spec", step="analyze", agent="architect")
        config = _resolve_function_call_path(
            FunctionCall(agent_name="architect"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
            prev,
        )
        assert config["config_callback"] == ""

    def test_no_prev_state_has_no_callback(self, tmp_path):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_function_call_path
        from nebuko.engine.lib.workflow_state import FunctionCall

        config = _resolve_function_call_path(
            FunctionCall(agent_name="context"),
            self._workflows_dir(tmp_path, self.SPEC_YAML),
        )
        assert config["config_callback"] == ""


class TestWorkflowCommandPerStepComplexity:
    """End-to-end tier pickup for ``/nebuko run workflow`` (issue #787).

    The issue's motivating case is the *first* step of a workflow: it never
    receives a turn outcome, so its declared ``complexity`` must reach the runtime
    override through ``CONFIG_COMPLEXITY`` via the workflow-command path.
    """

    _TIERED_YAML = (
        "name: tiered\n"
        'description: "tiered"\n'
        "steps:\n"
        "  - name: analyze\n"
        "    agent: architect\n"
        "    complexity: low\n"
        "    next: plan\n"
        "  - name: plan\n"
        "    agent: architect\n"
        "    next: null\n"
    )

    @staticmethod
    def _parse(body):
        from nebuko.engine.lib.resolve_config.sub_domains.workflow_command.workflow_command import (
            parse_workflow_command,
        )
        return parse_workflow_command(body)

    def _resolve(self, tmp_path, body):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import _resolve_human_path
        from nebuko.engine.lib.resolve_config.features.build_result.build_result import _build_result
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "tiered.yaml").write_text(self._TIERED_YAML)

        prompt_base = tmp_path / "prompts"
        prompt_base.mkdir(exist_ok=True)
        for name in TASK_TO_PROMPT.values():
            (prompt_base / name).write_text(f"# {name}")

        config = _resolve_human_path(
            body, None, None, str(wf_dir), workflow_command=self._parse(body),
        )
        return _build_result(config, str(prompt_base), str(wf_dir))

    def test_first_step_declared_tier_reaches_config_complexity(self, tmp_path):
        result = self._resolve(tmp_path, "/nebuko run workflow --name tiered")
        assert result["WORKFLOW_STEP"] == "analyze"
        assert result["CONFIG_COMPLEXITY"] == "low"

    def test_named_tiered_step_reaches_config_complexity(self, tmp_path):
        result = self._resolve(
            tmp_path, "/nebuko run workflow --name tiered --step analyze"
        )
        assert result["WORKFLOW_STEP"] == "analyze"
        assert result["CONFIG_COMPLEXITY"] == "low"

    def test_undeclared_step_defaults_to_medium(self, tmp_path):
        result = self._resolve(
            tmp_path, "/nebuko run workflow --name tiered --step plan"
        )
        assert result["WORKFLOW_STEP"] == "plan"
        assert result["CONFIG_COMPLEXITY"] == "medium"


_POLICY_WF_YAML = (
    "name: policy\n"
    "description: test\n"
    "trigger:\n"
    "  agents: [architect]\n"
    "steps:\n"
    "  - name: plan\n"
    "    agent: architect\n"
    "    next: implement\n"
    "    conditions:\n"
    "      - on_result: approved\n"
    "        next: review\n"
    "      - on_result: rejected\n"
    "        next: revise\n"
    "  - name: implement\n"
    "    agent: dev\n"
    "    next: null\n"
    "  - name: review\n"
    "    agent: review\n"
    "    next: null\n"
    "  - name: revise\n"
    "    agent: architect\n"
    "    next: null\n"
)


class TestRoutingPolicyTable:
    """One test per row of ``resolve_route``'s routing policy (issue #964).

    The policy table is the contract the migration lands on, so each row is
    exercised through the public ``resolve_config`` entry: a regression in the
    ordering (soft-kill > stop status > gate hold > verdict edge > linear edge)
    fails loudly. The ``plan`` step's linear edge (``implement``) deliberately
    differs from its ``approved`` edge (``review``), so a verdict that silently
    fell back to the linear edge cannot pass.

    Amended by #978: an *incomplete* turn never follows the linear edge. A
    ``FAILURE`` turn is a notification and holds for a human; ``INTERRUPTED``
    and ``SOFT_TIMEOUT`` retry the step in place; ``NO_OUTCOME`` (an agent that
    emitted no verdict at all) is also a notification, so a *bot* trigger
    carrying it performs no action. Only a ``COMPLETED`` verdict routes on the
    workflow edges.
    """

    @staticmethod
    def _dirs(tmp_path):
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

        prompt_base = tmp_path / "prompts"
        workflows_dir = tmp_path / "workflows"
        prompt_base.mkdir()
        workflows_dir.mkdir()
        for name in TASK_TO_PROMPT.values():
            (prompt_base / name).write_text("# prompt")
        (workflows_dir / "policy.yaml").write_text(_POLICY_WF_YAML)
        return str(prompt_base), str(workflows_dir)

    def _resolve(self, tmp_path, **outcome):
        import json as _json

        from nebuko.engine.lib.resolve_config import resolve_config

        state = {
            "version": 1,
            "workflow": "policy",
            "step": "plan",
            "agent": "architect",
            "turn_outcome": outcome,
        }
        block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        prompt_base, workflows_dir = self._dirs(tmp_path)
        body = "<!-- bot-workflow-terminal -->\n" + block
        return resolve_config(body, prompt_base, workflows_dir, is_bot=True)

    def test_approved_verdict_selects_the_on_result_edge(self, tmp_path):
        result = self._resolve(tmp_path, status="completed", appreciation="approved")
        assert result["WORKFLOW_STEP"] == "review"
        assert result["TASK"] == "review"

    def test_rejected_verdict_selects_the_on_result_edge(self, tmp_path):
        result = self._resolve(tmp_path, status="completed", appreciation="rejected")
        assert result["WORKFLOW_STEP"] == "revise"
        assert result["TASK"] == "architect"

    def test_hold_verdict_parks_the_step(self, tmp_path):
        result = self._resolve(
            tmp_path,
            status="completed",
            appreciation="hold",
            hold_kind="awaiting_input",
        )
        assert result["SKIP_AGENT"] == "true"
        assert result.get("WORKFLOW_STEP") != "review"

    def test_failure_without_a_verdict_parks_the_step(self, tmp_path):
        """A crashed run is a notification: no successor, no advance (#978)."""
        result = self._resolve(
            tmp_path, status="incomplete", incomplete_reason="failure"
        )
        assert result["SKIP_AGENT"] == "true"
        assert result.get("WORKFLOW_STEP") != "implement"

    def test_interrupted_without_a_verdict_retries_the_step_in_place(self, tmp_path):
        """An interrupted run decided nothing: retry in place, never advance (#978)."""
        result = self._resolve(
            tmp_path, status="incomplete", incomplete_reason="interrupted"
        )
        assert result["WORKFLOW_STEP"] == "plan"
        assert result["TASK"] == "architect"

    def test_no_outcome_without_a_verdict_is_a_notification(self, tmp_path):
        """A bot trigger with no verdict carries no dispatch information (#978).

        The linear-edge fallback is now reachable only from a *human* trigger
        (``resolve_config``'s legacy branch); a bot notification performs no
        action, exactly like a crashed run's comment.
        """
        result = self._resolve(
            tmp_path, status="incomplete", incomplete_reason="no_outcome"
        )
        assert result["SKIP_AGENT"] == "true"
        assert result.get("WORKFLOW_STEP") != "implement"

    def test_soft_timeout_retries_the_step_in_place(self, tmp_path):
        result = self._resolve(
            tmp_path, status="incomplete", incomplete_reason="soft_timeout"
        )
        assert result["WORKFLOW_STEP"] == "plan"
        assert result["TASK"] == "architect"


# A ``review`` step shaped exactly like ``defaults/workflows/default.yaml``:
# ``approved -> next: null`` is declared, ``rejected`` routes back, and there is
# no ``default`` condition and no linear ``next`` (issue #1006).
_VERDICT_FALLBACK_WF_YAML = (
    "name: fallback\n"
    "description: test\n"
    "steps:\n"
    "  - name: implement\n"
    "    agent: dev\n"
    "    next: review\n"
    "  - name: review\n"
    "    agent: review\n"
    "    conditions:\n"
    "      - on_result: approved\n"
    "        next: null\n"
    "      - on_result: rejected\n"
    "        next: implement\n"
)


# Same shape plus an explicit ``default`` edge (like ``think.yaml``): a
# verdict the step does not declare must take the fallback first (issue #1006).
# The step declares only ``rejected``, so ``approved`` is the unmatched key.
_VERDICT_FALLBACK_WITH_DEFAULT_WF_YAML = (
    "name: fallback_default\n"
    "description: test\n"
    "steps:\n"
    "  - name: implement\n"
    "    agent: dev\n"
    "    next: review\n"
    "  - name: review\n"
    "    agent: review\n"
    "    conditions:\n"
    "      - on_result: rejected\n"
    "        next: null\n"
    "      - on_result: default\n"
    "        next: implement\n"
)


# Same shape but the ``default`` edge itself is ``next: null``: a declared
# edge, so a verdict it matches is an honest finish rather than a gap (#1006).
_VERDICT_FALLBACK_WITH_NULL_DEFAULT_WF_YAML = (
    "name: fallback_default_null\n"
    "description: test\n"
    "steps:\n"
    "  - name: review\n"
    "    agent: review\n"
    "    conditions:\n"
    "      - on_result: approved\n"
    "        next: null\n"
    "      - on_result: default\n"
    "        next: null\n"
)


# The step declares only ``rejected -> next: null`` and no ``default`` edge, so
# ``approved`` is a verdict it does not name: nothing to fall back to but the
# benign ``null`` finish (issue #1006). ``hold`` cannot stand in here — the
# engine parks it before any routing, on both the agent and bot paths.
_VERDICT_NO_DEFAULT_WF_YAML = (
    "name: fallback_no_default\n"
    "description: test\n"
    "steps:\n"
    "  - name: review\n"
    "    agent: review\n"
    "    conditions:\n"
    "      - on_result: rejected\n"
    "        next: null\n"
)


class TestUnmatchedVerdictFallback:
    """Route -> ``default`` -> ``null``, never a hard failure (issue #1006).

    ``default.yaml``'s ``review`` step declares ``approved`` yet the run died
    with ``Resolution failed: step 'review' in workflow 'default' has no route
    for verdict 'approved'`` and exit 20. A *known* step whose verdict selects
    no edge is a config gap owed a warning, not a terminal error: it falls back
    to the step's ``default`` edge, and to a benign ``null`` finish when there
    is none. Only genuinely unknown workflow/step names keep failing.
    """

    @staticmethod
    def _dirs(tmp_path, yaml: str, name: str):
        from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

        prompt_base = tmp_path / "prompts"
        workflows_dir = tmp_path / "workflows"
        prompt_base.mkdir(exist_ok=True)
        workflows_dir.mkdir(exist_ok=True)
        for task in TASK_TO_PROMPT.values():
            (prompt_base / task).write_text("# prompt")
        (workflows_dir / f"{name}.yaml").write_text(yaml)
        return str(prompt_base), str(workflows_dir)

    def _resolve(self, tmp_path, workflow: str, step: str, yaml: str, name: str, **outcome):
        import json as _json

        from nebuko.engine.lib.resolve_config import resolve_config

        state = {
            "version": 1,
            "workflow": workflow,
            "step": step,
            "agent": "review",
            "turn_outcome": outcome,
        }
        block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        prompt_base, workflows_dir = self._dirs(tmp_path, yaml, name)
        body = "<!-- bot-workflow-terminal -->\n" + block
        return resolve_config(body, prompt_base, workflows_dir, is_bot=True)

    def _review(self, tmp_path, verdict: str, **extra):
        return self._resolve(
            tmp_path,
            "fallback",
            "review",
            _VERDICT_FALLBACK_WF_YAML,
            "fallback",
            status="completed",
            appreciation=verdict,
            **extra,
        )

    def test_declared_verdict_is_routed(self, tmp_path):
        # Regression guard (issues #924/#935): the declared ``rejected`` edge
        # must win, never be swallowed by the fallback chain.
        result = self._review(tmp_path, "rejected")
        assert result.get("TASK") == "dev"
        assert result["WORKFLOW_STEP"] == "implement"

    def test_declared_null_verdict_is_a_benign_finish(self, tmp_path, capsys):
        # Regression guard (issues #924/#1006): ``approved -> next: null`` is a
        # *declared* edge, so it must stop the run quietly — no "no route for
        # verdict" warning, no fallback, no error.
        result = self._review(tmp_path, "approved")
        assert result["SKIP_AGENT"] == "true"
        assert result.get("SKIP_KIND") == "terminal"
        assert result["REASON"] == "Terminal step reached"
        assert "no route for verdict" not in result["REASON"]
        assert "falling back" not in result["REASON"]
        assert "Warning:" not in capsys.readouterr().err

    def test_unmatched_verdict_without_default_is_a_benign_finish(self, tmp_path):
        # The bug of issue #1006: a verdict the step does not declare used to
        # raise ``Resolution failed: ... has no route for verdict`` and exit 20.
        # This ``review`` step declares only ``rejected``, so ``approved`` is
        # unmatched and the run must fall back to a benign null finish.
        result = self._resolve(
            tmp_path,
            "fallback_no_default",
            "review",
            _VERDICT_NO_DEFAULT_WF_YAML,
            "fallback_no_default",
            status="completed",
            appreciation="approved",
        )
        assert result["SKIP_AGENT"] == "true"
        assert result.get("SKIP_KIND") == "terminal"
        assert "falling back to a null finish" in result["REASON"]
        assert "Resolution failed" not in result["REASON"]
        assert result.get("TASK") is None

    def test_unmatched_verdict_takes_the_default_edge_when_declared(self, tmp_path):
        result = self._resolve(
            tmp_path,
            "fallback_default",
            "review",
            _VERDICT_FALLBACK_WITH_DEFAULT_WF_YAML,
            "fallback_default",
            status="completed",
            appreciation="approved",
        )
        assert result["WORKFLOW_STEP"] == "implement"
        assert result.get("TASK") == "dev"

    def test_declared_default_null_edge_is_a_benign_fallback(self, tmp_path, capsys):
        # The step declares ``default -> next: null``, so a verdict it does not
        # name falls back to that declared null finish: benign (exit 0), with
        # the warning naming the step, verdict and chosen fallback.
        result = self._resolve(
            tmp_path,
            "fallback_default_null",
            "review",
            _VERDICT_FALLBACK_WITH_NULL_DEFAULT_WF_YAML,
            "fallback_default_null",
            status="completed",
            appreciation="rejected",
        )
        assert result["SKIP_AGENT"] == "true"
        assert result.get("SKIP_KIND") == "terminal"
        assert "falling back to a null finish" in result["REASON"]
        assert "Resolution failed" not in result["REASON"]
        assert "Warning:" in capsys.readouterr().err

    def test_unknown_step_still_fails_resolution(self, tmp_path):
        result = self._resolve(
            tmp_path,
            "fallback",
            "no_such_step",
            _VERDICT_FALLBACK_WF_YAML,
            "fallback",
            status="completed",
            appreciation="approved",
        )
        assert result["SKIP_AGENT"] == "true"
        assert "Resolution failed" in result["REASON"]
        assert result.get("SKIP_KIND") != "terminal"


