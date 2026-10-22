import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workflow_state import (
    WorkflowState,
    Appreciation,
    HistoryEntry,
    StateMetrics,
    TurnOutcome,
    TurnStatus,
    workflow_ref_from_env,
)
from workflow_state.sub_domains.gate_decision import GateDecision


def _outcome() -> TurnOutcome:
    """A completed/approved outcome — the mandatory routing fact (issue #964)."""
    return TurnOutcome(status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED)


class TestWorkflowStateSchema(unittest.TestCase):
    """Test WorkflowState Pydantic schema validation."""

    def test_minimal_state(self):
        state = WorkflowState(workflow="test", step="analyze", agent="architect")
        self.assertEqual(state.run_id, 0)
        self.assertEqual(state.schema_version, 0)
        self.assertEqual(state.phase, "")
        self.assertEqual(state.history, [])
        self.assertEqual(state.pinned_comment_ids, [])
        self.assertIsNone(state.parent_workflow)

    def test_full_state(self):
        state = WorkflowState(
            workflow="default",
            step="implement",
            agent="dev",
            run_id=5,
            schema_version=4,
            phase="execution",
            history=[
                HistoryEntry(
                    step="analyze",
                    agent="architect",
                    summary="Analyzed issue",
                    turn_outcome=_outcome(),
                    metrics=StateMetrics(input_tokens=1000, output_tokens=500),
                )
            ],
            pinned_comment_ids=[12345],
            current_context={"task": "implement feature"},
            parent_workflow="spec-driven-design",
            parent_workflow_step="stub_phase",
        )
        self.assertEqual(state.run_id, 5)
        self.assertEqual(len(state.history), 1)
        self.assertIn(12345, state.pinned_comment_ids)
        # Per-step metrics live on the history entry (issue #792), not the root.
        self.assertEqual(state.history[-1].metrics.input_tokens, 1000)
        self.assertFalse(hasattr(state, "metrics"))

    def test_defaults_for_optional_fields(self):
        """Step, agent, phase default to empty string for backward compat."""
        state = WorkflowState(workflow="test", agent="architect")
        self.assertEqual(state.step, "")
        self.assertEqual(state.phase, "")
        self.assertEqual(state.run_id, 0)

    def test_to_json_round_trip_preserves_position(self):
        """Populated v3 position + history position survive to_json ->
        re-validate (issue #638): exclude_defaults must NOT collapse a
        non-empty refs list — the history[-1].position == position
        invariant depends on this seam."""
        import json
        from config import StepRef
        from workflow_state.workflow_state import to_json

        refs = [
            StepRef(workflow="plan-orchestration", step="sub_impl"),
            StepRef(workflow="dev-review-loop", step="implement"),
        ]
        state = WorkflowState(
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            position=refs,
            history=[
                HistoryEntry(
                    step="implement", agent="dev",
                    summary="impl", position=refs,
                    turn_outcome=_outcome(),
                )
            ],
        )
        serialized = to_json(state)
        # Byte-identical round trip: position + entry position survive.
        back = WorkflowState.model_validate(json.loads(serialized))
        self.assertEqual(back.position, refs)
        self.assertEqual(back.history[-1].position, refs)

    def test_to_json_round_trip_preserves_history_metrics(self):
        """Per-step metrics survive to_json -> re-validate (issue #792): the
        comment-embedded state JSON is now the single metrics authority, so
        exclude_defaults must NOT collapse a populated metrics object."""
        import json
        from workflow_state.workflow_state import to_json

        metrics = StateMetrics(
            run_id="run-1",
            model_name="default",
            input_tokens=1234,
            output_tokens=567,
            complexity="medium",
            tool_calls_per_tool={"terminal": 3},
            agent_runtimes_per_call_ms=[10.0, 20.0],
        )
        state = WorkflowState(
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            history=[HistoryEntry(step="implement", agent="dev", turn_outcome=_outcome(), metrics=metrics)],
        )
        raw = json.loads(to_json(state))
        self.assertEqual(raw["history"][-1]["metrics"]["input_tokens"], 1234)
        self.assertEqual(raw["history"][-1]["metrics"]["tool_calls_per_tool"], {"terminal": 3})
        back = WorkflowState.model_validate(raw)
        self.assertEqual(back.history[-1].metrics, metrics)

    def test_v2_position_migration_does_not_mutate_input_dict(self):
        """The position migration validator must not destroy the caller's
        raw dict (issue #638, review fix round 1): a freshly json.load-ed
        state file must stay json.dumps-able after validation."""
        import json

        raw = {
            "workflow": "dev-review-loop",
            "step": "implement",
            "agent": "dev",
            "position": {
                "version": 2,
                "workflow": "dev-review-loop",
                "step": "implement",
                "workflow_name": "plan-orchestration",
                "step_name": "sub_impl",
                "step": {
                    "workflow_name": "dev-review-loop",
                    "step_name": "implement",
                },
            },
        }
        original = json.dumps(raw)
        state = WorkflowState.model_validate(raw)
        # Migrated to the flat v3 refs (outermost-first chain walk).
        self.assertEqual(
            [(r.workflow, r.step) for r in state.position],
            [("plan-orchestration", "sub_impl"),
             ("dev-review-loop", "implement")],
        )
        # Caller's dict untouched — still serializes to the same payload.
        self.assertEqual(json.dumps(raw), original)


class TestWorkflowStateGateFields(unittest.TestCase):
    """Engine-owned gate decision fields survive state serialization (#648)."""

    @staticmethod
    def _decision(status: str, step: str = "plan") -> GateDecision:
        from datetime import datetime, timezone
        from config import StepRef

        return GateDecision(
            status=status,
            workflow="default",
            target_step=step,
            decided_by="olivier-norture",
            decided_at=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            position=[StepRef(workflow="default", step=step)],
        )

    def test_gate_fields_default_to_none_and_empty(self):
        """A fresh state carries no decision and an empty audit."""
        state = WorkflowState(workflow="default", step="plan")
        self.assertIsNone(state.gate_decision)
        self.assertEqual(state.gate_history, [])

    def test_legacy_blob_without_gate_keys_loads_with_defaults(self):
        """Older state blobs (no gate_decision / gate_history) still load."""
        raw = {"workflow": "default", "step": "plan", "agent": "architect"}
        state = WorkflowState.model_validate(raw)
        self.assertIsNone(state.gate_decision)
        self.assertEqual(state.gate_history, [])

    def test_gate_history_preserves_append_order(self):
        approved = self._decision("approved", "plan")
        rejected = self._decision("rejected", "stub_approval")
        state = WorkflowState(
            workflow="default",
            step="implement",
            gate_decision=rejected,
            gate_history=[approved, rejected],
        )
        self.assertEqual(state.gate_decision, rejected)
        self.assertEqual(state.gate_history, [approved, rejected])

    def test_to_json_round_trip_preserves_gate_fields(self):
        """The <details> state JSON carries the decision and the full audit."""
        import json
        from workflow_state.workflow_state import to_json

        approved = self._decision("approved", "plan")
        rejected = self._decision("rejected", "stub_approval")
        state = WorkflowState(
            workflow="default",
            step="implement",
            gate_decision=rejected,
            gate_history=[approved, rejected],
        )
        raw = json.loads(to_json(state))
        self.assertEqual(raw["gate_decision"]["status"], "rejected")
        self.assertEqual([d["status"] for d in raw["gate_history"]], ["approved", "rejected"])
        back = WorkflowState.model_validate(raw)
        self.assertEqual(back.gate_decision, rejected)
        self.assertEqual(back.gate_history, [approved, rejected])

    def test_to_json_omits_empty_gate_fields(self):
        """Defaults are excluded, so legacy states stay byte-identical."""
        import json
        from workflow_state.workflow_state import to_json

        raw = json.loads(to_json(WorkflowState(workflow="default", step="plan")))
        self.assertNotIn("gate_decision", raw)
        self.assertNotIn("gate_history", raw)


class TestStateMetricsCost(unittest.TestCase):
    """Cost is carried through the compact workflow-state metrics."""

    def test_cost_defaults_to_zero(self):
        self.assertEqual(StateMetrics().cost, 0.0)

    def test_cost_extracted_from_full_metrics_dict(self):
        metrics = StateMetrics.from_full_metrics_dict(
            {"input_tokens": 10, "output_tokens": 5, "cost": 0.1234}
        )
        self.assertEqual(metrics.cost, 0.1234)

    def test_cost_defaults_when_absent(self):
        """Legacy metric dicts without 'cost' still load (backward compat)."""
        metrics = StateMetrics.from_full_metrics_dict({"input_tokens": 10})
        self.assertEqual(metrics.cost, 0.0)


class TestStateMetricsCacheAndReasoning(unittest.TestCase):
    """Root cache/reasoning counters survive the state mirror (issue #917)."""

    def test_counters_extracted_from_full_metrics_dict(self):
        metrics = StateMetrics.from_full_metrics_dict(
            {
                "input_tokens": 26493,
                "output_tokens": 2772,
                "cache_hit_rate": 0.4819,
                "cache_hit_tokens": 4864,
                "reasoning_tokens": 120,
            }
        )
        self.assertEqual(metrics.cache_hit_tokens, 4864)
        self.assertEqual(metrics.reasoning_tokens, 120)
        self.assertEqual(metrics.cache_hit_rate, 0.4819)

    def test_counters_default_when_absent(self):
        """Legacy payloads without the new keys still load (backward compat)."""
        metrics = StateMetrics.from_full_metrics_dict({"input_tokens": 10})
        self.assertEqual(metrics.cache_hit_tokens, 0)
        self.assertEqual(metrics.reasoning_tokens, 0)
        self.assertEqual(metrics.cache_hit_rate, 0.0)


class TestStateMetricsProviderHealth(unittest.TestCase):
    """TPS/TTFT survive the workflow-state metrics mirror (issue #829)."""

    def test_provider_health_extracted_from_full_metrics_dict(self):
        metrics = StateMetrics.from_full_metrics_dict(
            {
                "input_tokens": 10,
                "tokens_per_second": 137.5,
                "time_to_first_token": 0.62,
            }
        )
        self.assertEqual(metrics.tokens_per_second, 137.5)
        self.assertEqual(metrics.time_to_first_token, 0.62)

    def test_provider_health_default_to_none(self):
        metrics = StateMetrics.from_full_metrics_dict({"input_tokens": 10})
        self.assertIsNone(metrics.tokens_per_second)
        self.assertIsNone(metrics.time_to_first_token)

    def test_provider_health_round_trips_from_metrics_to_dict(self):
        from metrics_schema import Metrics

        original = Metrics(
            run_id="r1",
            model_name="gpt-4",
            agent_role="dev",
            tokens_per_second=137.5,
            time_to_first_token=0.62,
        )
        metrics = StateMetrics.from_full_metrics_dict(original.to_dict())
        self.assertEqual(metrics.tokens_per_second, 137.5)
        self.assertEqual(metrics.time_to_first_token, 0.62)


class TestStateMetricsToolTime(unittest.TestCase):
    """Tool execution time survives the workflow-state metrics mirror (#1016)."""

    def test_tool_time_extracted_from_full_metrics_dict(self):
        metrics = StateMetrics.from_full_metrics_dict(
            {
                "input_tokens": 10,
                "tool_time_per_tool": {"terminal": {"total_ms": 1200.0, "count": 3}},
                "tool_runtime_ms": 1200.0,
                "wall_clock_ms": 45000.0,
            }
        )
        self.assertEqual(
            metrics.tool_time_per_tool, {"terminal": {"total_ms": 1200.0, "count": 3}}
        )
        self.assertEqual(metrics.tool_runtime_ms, 1200.0)
        self.assertEqual(metrics.wall_clock_ms, 45000.0)

    def test_tool_time_defaults_when_absent(self):
        metrics = StateMetrics.from_full_metrics_dict({"input_tokens": 10})
        self.assertEqual(metrics.tool_time_per_tool, {})
        self.assertEqual(metrics.tool_runtime_ms, 0.0)
        self.assertEqual(metrics.wall_clock_ms, 0.0)

    def test_tool_time_round_trips_from_metrics_to_dict(self):
        from metrics_schema import Metrics

        original = Metrics(
            run_id="r1",
            model_name="gpt-4",
            agent_role="dev",
            tool_time_per_tool={"bash": {"total_ms": 250.5, "count": 1}},
            tool_runtime_ms=250.5,
            wall_clock_ms=3000.0,
        )
        metrics = StateMetrics.from_full_metrics_dict(original.to_dict())
        self.assertEqual(
            metrics.tool_time_per_tool, {"bash": {"total_ms": 250.5, "count": 1}}
        )
        self.assertEqual(metrics.tool_runtime_ms, 250.5)
        self.assertEqual(metrics.wall_clock_ms, 3000.0)


class TestWorkflowRefProvenance(unittest.TestCase):
    """The pinned ref of the reusable workflow survives the state codec
    (issue #862). It is provenance only: the checkout pin is resolved by the
    workflow YAML, but a run should record which workflow version produced it.
    """

    def test_defaults_to_none(self):
        state = WorkflowState(workflow="default", step="analyze", agent="architect")
        self.assertIsNone(state.workflow_ref)

    def test_legacy_blob_without_field_loads_none(self):
        data = {
            "version": 3,
            "workflow": "default",
            "step": "analyze",
            "agent": "architect",
            "history": [
                {
                    "step": "analyze",
                    "agent": "architect",
                    "summary": "ok",
                    "turn_outcome": {"status": "completed", "appreciation": "approved"},
                }
            ],
        }
        state = WorkflowState.model_validate(data)
        self.assertIsNone(state.workflow_ref)

    def test_round_trip_preserves_value(self):
        import json
        from workflow_state.workflow_state import to_json

        ref = "octocat/hello-world/.github/workflows/nebuko-workflow.yaml@refs/tags/v1.2"
        state = WorkflowState(
            workflow="default",
            step="analyze",
            agent="architect",
            workflow_ref=ref,
        )
        raw = json.loads(to_json(state))
        self.assertEqual(raw["workflow_ref"], ref)
        back = WorkflowState.model_validate(raw)
        self.assertEqual(back.workflow_ref, ref)

    def test_to_json_omits_none_workflow_ref(self):
        import json
        from workflow_state.workflow_state import to_json

        state = WorkflowState(workflow="default", step="analyze", agent="architect")
        self.assertNotIn("workflow_ref", json.loads(to_json(state)))


class TestWorkflowRefFromEnv(unittest.TestCase):
    """``NEBUKO_WORKFLOW_REF`` -> ``workflow_ref`` (issue #862)."""

    def test_returns_value_when_present(self):
        ref = "o/r/.github/workflows/nebuko-workflow.yaml@refs/heads/main"
        self.assertEqual(workflow_ref_from_env({"NEBUKO_WORKFLOW_REF": ref}), ref)

    def test_absent_maps_to_none(self):
        self.assertIsNone(workflow_ref_from_env({}))

    def test_empty_string_maps_to_none(self):
        self.assertIsNone(workflow_ref_from_env({"NEBUKO_WORKFLOW_REF": ""}))

    def test_whitespace_only_maps_to_none(self):
        self.assertIsNone(workflow_ref_from_env({"NEBUKO_WORKFLOW_REF": "   "}))


class TestStateMetricsModelExecutionConfig(unittest.TestCase):
    """The applied model execution config survives the state mirror (#820)."""

    def test_defaults_to_none(self):
        self.assertIsNone(StateMetrics().model_execution_config)
        self.assertIsNone(
            StateMetrics.from_full_metrics_dict({"input_tokens": 10}).model_execution_config
        )

    def test_round_trips_from_metrics_to_dict(self):
        from metrics_schema import Metrics

        original = Metrics(
            run_id="r1",
            model_name="openai/glm-4.6",
            agent_role="dev",
            model_execution_config={
                "model_path": "openai/glm-4.6",
                "provider": "openrouter",
                "temperature": 0.7,
                "top_p": 0.8,
                "complexity_tier": "high",
                "model_source": "complexity_override",
            },
        )

        metrics = StateMetrics.from_full_metrics_dict(original.to_dict())

        config = metrics.model_execution_config
        self.assertEqual(config.model_path, "openai/glm-4.6")
        self.assertEqual(config.temperature, 0.7)
        self.assertEqual(config.top_p, 0.8)
        self.assertEqual(config.complexity_tier, "high")
        self.assertEqual(config.model_source, "complexity_override")
        # Serialized straight into the history JSON with no extra mapping.
        self.assertEqual(
            metrics.model_dump()["model_execution_config"]["model_path"],
            "openai/glm-4.6",
        )

    def test_malformed_payload_is_ignored(self):
        metrics = StateMetrics.from_full_metrics_dict(
            {"input_tokens": 10, "model_execution_config": "not-a-dict"}
        )
        self.assertIsNone(metrics.model_execution_config)

