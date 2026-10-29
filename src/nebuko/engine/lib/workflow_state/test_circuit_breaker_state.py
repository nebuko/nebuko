"""Per-issue circuit-breaker policy (issue #839).

The breaker mechanism recounts bot markers every run; this persisted state is
the per-issue *policy source* that lets a human raise (or tighten) the call
budget for a single issue without touching the ``circuit_breaker_threshold``
workflow parameter.
"""

import os
import sys
import unittest


from nebuko.engine.lib.workflow_state import CircuitBreakerState, WorkflowState, build_state, to_json


class TestCircuitBreakerStateModel(unittest.TestCase):
    def test_default_limit_is_none(self):
        self.assertIsNone(CircuitBreakerState().limit)

    def test_workflow_state_default_fills_circuit_breaker(self):
        self.assertEqual(WorkflowState().circuit_breaker, CircuitBreakerState())

    def test_legacy_state_without_key_loads(self):
        legacy = WorkflowState.model_validate(
            {"version": 3, "workflow": "spec-driven-impl", "step": "stub"}
        )
        self.assertIsNone(legacy.circuit_breaker.limit)

    def test_explicit_circuit_breaker_roundtrips(self):
        state = WorkflowState.model_validate({"circuit_breaker": {"limit": 35}})
        self.assertEqual(state.circuit_breaker.limit, 35)


class TestCircuitBreakerStateEffective(unittest.TestCase):
    def test_override_wins_over_env_default(self):
        self.assertEqual(CircuitBreakerState(limit=35).effective(25), 35)

    def test_none_inherits_env_default(self):
        self.assertEqual(CircuitBreakerState().effective(25), 25)

    def test_none_inherits_a_custom_env_default(self):
        # A host running a custom workflow parameter must not be regressed to
        # 25 by a state blob predating this feature.
        self.assertEqual(CircuitBreakerState().effective(40), 40)

    def test_override_can_tighten_below_env_default(self):
        self.assertEqual(CircuitBreakerState(limit=5).effective(25), 5)


class TestCircuitBreakerStateWithLimit(unittest.TestCase):
    def test_with_limit_returns_a_new_instance(self):
        base = CircuitBreakerState()
        updated = base.with_limit(35)
        self.assertEqual(updated, CircuitBreakerState(limit=35))

    def test_with_limit_does_not_mutate_the_receiver(self):
        base = CircuitBreakerState(limit=20)
        base.with_limit(35)
        self.assertEqual(base, CircuitBreakerState(limit=20))


class TestBuildStateCarriesCircuitBreaker(unittest.TestCase):
    def test_build_state_default_fills_empty_policy(self):
        state = build_state(None, workflow="w", step="implement", agent="dev")
        self.assertEqual(state.circuit_breaker, CircuitBreakerState())

    def test_build_state_preserves_previous_policy(self):
        prev = build_state(None, workflow="w", step="implement", agent="dev")
        prev = prev.model_copy(update={"circuit_breaker": CircuitBreakerState(limit=35)})
        nxt = build_state(prev, workflow="w", step="implement", agent="dev")
        self.assertEqual(nxt.circuit_breaker.limit, 35)


class TestLegacySerialization(unittest.TestCase):
    def test_legacy_blob_serializes_identically(self):
        # default_factory + exclude_defaults keep a predating blob byte-identical
        # on read: the new key must not appear in the JSON.
        legacy = WorkflowState.model_validate(
            {"version": 3, "workflow": "spec-driven-impl", "step": "stub"}
        )
        self.assertNotIn("circuit_breaker", to_json(legacy))

    def test_explicit_override_is_serialized(self):
        state = WorkflowState.model_validate({"circuit_breaker": {"limit": 35}})
        self.assertIn("circuit_breaker", to_json(state))


if __name__ == "__main__":
    unittest.main()
