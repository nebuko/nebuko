#!/usr/bin/env python3
"""Tests for state_manager.py — workflow state management module.

Tests cover:
- WorkflowState schema validation
- build_state() transitions (initial, append, pin, phase)
- wrap_comment_with_state() output format
- extract_state_from_body() parsing
- strip_state_section() removal
- extract_legacy_metrics() backward compat
- state_roundtrip serialization
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_manager import (
    WorkflowState,
    HistoryEntry,
    StateMetrics,
    build_state,
    wrap_comment_with_state,
    extract_state,
    extract_state_from_body,
    strip_state_section,
    extract_legacy_metrics,
)


class TestWorkflowStateSchema(unittest.TestCase):
    """Test WorkflowState Pydantic schema validation."""

    def test_minimal_state(self):
        state = WorkflowState(workflow="test", step="analyze", agent="genius")
        self.assertEqual(state.version, 1)
        self.assertEqual(state.phase, "")
        self.assertEqual(state.history, [])
        self.assertEqual(state.pinned_comment_ids, [])
        self.assertIsNone(state.parent_workflow)

    def test_full_state(self):
        state = WorkflowState(
            workflow="default",
            step="implement",
            agent="dev",
            version=5,
            phase="execution",
            history=[HistoryEntry(step="analyze", agent="genius", summary="Analyzed issue")],
            pinned_comment_ids=[12345],
            current_context={"task": "implement feature"},
            metrics=StateMetrics(input_tokens=1000, output_tokens=500),
            parent_workflow="spec-driven-design",
            parent_workflow_step="stub_phase",
        )
        self.assertEqual(state.version, 5)
        self.assertEqual(len(state.history), 1)
        self.assertIn(12345, state.pinned_comment_ids)

    def test_defaults_for_optional_fields(self):
        """Step, agent, phase default to empty string for backward compat."""
        state = WorkflowState(workflow="test", agent="genius")
        self.assertEqual(state.step, "")
        self.assertEqual(state.phase, "")
        self.assertEqual(state.version, 1)


class TestBuildState(unittest.TestCase):
    """Test build_state() pure state-transition function."""

    def test_initial_state(self):
        state = build_state(None, workflow="default", step="analyze", agent="genius")
        self.assertEqual(state.version, 1)
        self.assertEqual(state.workflow, "default")
        self.assertEqual(state.step, "analyze")
        self.assertEqual(state.agent, "genius")
        self.assertEqual(len(state.history), 1)

    def test_append_to_existing(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
            summary="Analyzed request",
        )
        next_state = build_state(
            prev,
            workflow="default",
            step="context",
            agent="ctx",
            summary="Collected files",
        )
        self.assertEqual(next_state.version, 2)
        self.assertEqual(len(next_state.history), 2)
        self.assertEqual(next_state.history[0].summary, "Analyzed request")
        self.assertEqual(next_state.history[1].summary, "Collected files")

    def test_phase_derivation(self):
        state = build_state(
            None,
            workflow="default",
            step="implement",
            agent="dev",
            phase="execution",
        )
        self.assertEqual(state.phase, "execution")

    def test_pinning(self):
        state = build_state(
            None,
            workflow="default",
            step="plan",
            agent="genius",
            summary="Architecture proposal",
            pin=True,
        )
        self.assertTrue(state.history[0].pin)

    def test_metrics_included(self):
        metrics_dict = {"input_tokens": 5000, "output_tokens": 2000}
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
            metrics_dict=metrics_dict,
        )
        self.assertIsNotNone(state.metrics)
        self.assertEqual(state.metrics.input_tokens, 5000)


class TestWrapCommentWithState(unittest.TestCase):
    """Test collapsed section formatting."""

    def test_produces_collapsed_section(self):
        state = WorkflowState(workflow="default", step="plan", agent="genius")
        result = wrap_comment_with_state(state, "Hello world")
        self.assertIn("<details", result)
        self.assertIn("Workflow State", result)
        self.assertIn("Hello world", result)

    def test_json_between_markers(self):
        state = WorkflowState(workflow="default", step="plan", agent="genius")
        result = wrap_comment_with_state(state, "")
        self.assertIn("<!-- WORKFLOW_STATE_START -->", result)
        self.assertIn("<!-- WORKFLOW_STATE_END -->", result)

    def test_compact_json(self):
        state = WorkflowState(workflow="default", step="plan", agent="genius")
        result = wrap_comment_with_state(state, "")
        json_str = result.split("<!-- WORKFLOW_STATE_START -->")[1].split("<!-- WORKFLOW_STATE_END -->")[0]
        parsed = json.loads(json_str.strip())
        self.assertEqual(parsed["workflow"], "default")


class TestExtractStateFromBody(unittest.TestCase):
    """Test extracting state from comment bodies."""

    def _make_wrapped(self, state, body=""):
        return wrap_comment_with_state(state, body)

    def test_extract_from_wrapped_comment(self):
        state = WorkflowState(
            workflow="default",
            step="plan",
            agent="genius",
            version=3,
            history=[HistoryEntry(step="analyze", agent="genius", summary="Analyzed")],
        )
        wrapped = self._make_wrapped(state, "Some markdown content here")
        extracted = extract_state_from_body(wrapped)
        self.assertIsNotNone(extracted)
        self.assertEqual(extracted.workflow, "default")
        self.assertEqual(extracted.version, 3)
        self.assertEqual(len(extracted.history), 1)

    def test_returns_none_when_no_state(self):
        result = extract_state_from_body("Just plain text, no state here")
        self.assertIsNone(result)

    def test_returns_none_for_empty_body(self):
        self.assertIsNone(extract_state_from_body(""))
        self.assertIsNone(extract_state_from_body(None))


class TestStripStateSection(unittest.TestCase):
    """Test removing state section from comment bodies."""

    def _make_wrapped(self, state, body=""):
        return wrap_comment_with_state(state, body)

    def test_removes_details_block(self):
        state = WorkflowState(workflow="default", step="plan", agent="genius")
        wrapped = self._make_wrapped(state, "Body content")
        stripped = strip_state_section(wrapped)
        self.assertIn("Body content", stripped)
        self.assertNotIn("WORKFLOW_STATE_START", stripped)
        self.assertNotIn("<details", stripped)

    def test_preserves_body_when_no_state(self):
        original = "No state here, just regular content"
        self.assertEqual(strip_state_section(original), original)

    def test_handles_empty_string(self):
        self.assertEqual(strip_state_section(""), "")


class TestExtractLegacyMetrics(unittest.TestCase):
    """Test backward-compatible legacy metrics extraction."""

    def test_extracts_metrics_from_html_comment(self):
        body = "Some text\n<!-- agent-metrics: {\"run_id\": \"abc-123\", \"input_tokens\": 5000} -->"
        result = extract_legacy_metrics(body)
        self.assertIn("run_id", result)
        self.assertEqual(result["input_tokens"], 5000)

    def test_returns_empty_dict_when_no_metrics(self):
        result = extract_legacy_metrics("No metrics here")
        self.assertEqual(result, {})

    def test_ignores_broken_json(self):
        body = "<!-- agent-metrics: {broken json} -->"
        result = extract_legacy_metrics(body)
        self.assertEqual(result, {})


class TestStateRoundtrip(unittest.TestCase):
    """Test serialization roundtrip via JSON."""

    def test_roundtrip_preserves_data(self):
        original = WorkflowState(
            workflow="default",
            step="review",
            agent="review",
            version=5,
            phase="review",
            history=[
                HistoryEntry(step="analyze", agent="genius", summary="Analyzed"),
                HistoryEntry(step="plan", agent="genius", summary="Planned", pin=True),
            ],
            pinned_comment_ids=[123, 456],
            current_context={"approved": True},
            metrics=StateMetrics(input_tokens=3000, output_tokens=1500, run_id="test-run"),
        )

        # Serialize
        json_str = original.model_dump_json(exclude_defaults=True)
        restored = WorkflowState.model_validate_json(json_str)

        self.assertEqual(restored.workflow, original.workflow)
        self.assertEqual(restored.step, original.step)
        self.assertEqual(restored.version, original.version)
        self.assertEqual(restored.phase, original.phase)
        self.assertEqual(len(restored.history), len(original.history))
        self.assertTrue(restored.history[1].pin)
        self.assertEqual(restored.pinned_comment_ids, [123, 456])
        self.assertEqual(restored.current_context["approved"], True)
        self.assertIsNotNone(restored.metrics)
        self.assertEqual(restored.metrics.input_tokens, 3000)


class TestExtractState(unittest.TestCase):
    """Test extract_state() — the primary entry point that works with a list of comment dicts."""

    def _make_state(self, version: int) -> WorkflowState:
        """Create a WorkflowState with the given version by chaining build_state calls."""
        state = None
        for i in range(1, version + 1):
            state = build_state(state, agent="test")
        return state

    def _make_state_body(self, version: int = 1) -> str:
        """Create a comment body containing an embedded workflow state section."""
        state = self._make_state(version)
        return wrap_comment_with_state(state, "Some agent message content")

    def test_no_comments_returns_none(self):
        self.assertIsNone(extract_state([]))

    def test_single_comment_without_state_returns_none(self):
        comments = [{"body": "Just a regular comment"}]
        self.assertIsNone(extract_state(comments))

    def test_single_comment_with_state_returns_state(self):
        body = self._make_state_body(version=3)
        comments = [{"id": 100, "body": body}]
        state = extract_state(comments)
        self.assertIsNotNone(state)
        self.assertEqual(state.version, 3)
        self.assertEqual(state.agent, "test")

    def test_multiple_comments_returns_last_with_state(self):
        body1 = self._make_state_body(version=1)
        body2 = "No state here"
        body3 = self._make_state_body(version=2)
        comments = [
            {"id": 100, "body": body1},
            {"id": 200, "body": body2},
            {"id": 300, "body": body3},
        ]
        state = extract_state(comments)
        self.assertIsNotNone(state)
        self.assertEqual(state.version, 2)  # Last one wins

    def test_mixed_comments_skips_non_state(self):
        body_v1 = self._make_state_body(version=1)
        body_v5 = self._make_state_body(version=5)
        comments = [
            {"id": 10, "body": "Hello"},
            {"id": 20, "body": body_v1},
            {"id": 30, "body": "World"},
            {"id": 40, "body": body_v5},
            {"id": 50, "body": "End"},
        ]
        state = extract_state(comments)
        self.assertIsNotNone(state)
        self.assertEqual(state.version, 5)

    def test_empty_body_does_not_crash(self):
        comments = [{"id": 1, "body": ""}]
        self.assertIsNone(extract_state(comments))

    def test_none_body_does_not_crash(self):
        comments = [{"id": 1, "body": None}]
        self.assertIsNone(extract_state(comments))


if __name__ == "__main__":
    unittest.main()
