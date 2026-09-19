#!/usr/bin/env python3
"""Tests for workflow_state.py — workflow state management module.

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
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from workflow_state import (
    Dispatch,
    WorkflowState,
    HistoryEntry,
    StateMetrics,
    IssueInfo,
    PullRequestInfo,
    build_state,
    extract_latest_state_from_comments as extract_state,
    extract_state_from_body,
    extract_dispatch_from_state,
    to_git_comment,
)
from workflow_state.features.comment.to_git_comment import (
    collapse_sections,
    strip_state_section,
    wrap_comment_with_state,
)
from workflow_state.sub_domains.state_metrics.features.legacy.legacy import (
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

    def test_scaffold_branch_defaults_to_none(self):
        """scaffold_branch is unset until the scaffold step runs (issue #604)."""
        state = WorkflowState(workflow="test", agent="genius")
        self.assertIsNone(state.scaffold_branch)


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


class TestBuildStateStepFallback(unittest.TestCase):
    """Regression (issue #458): build_state must never drop the current step
    when the explicit arg is empty but a previous value is recoverable."""

    def test_build_state_falls_back_to_prev_step_when_empty(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
            summary="Analyzed request",
        )
        state = build_state(prev, step="")
        self.assertEqual(state.step, "analyze")

    def test_build_state_falls_back_to_last_history_entry(self):
        """Exact #457 v2 shape: prev state with step='' whose history ends at
        {step='context', agent='ctx'} — result must recover step and agent."""
        prev = WorkflowState(
            version=2,
            workflow="spec-driven-impl",
            step="",
            agent="ctx",
            history=[
                HistoryEntry(step="analyze", agent="genius", summary="Analyzed"),
                HistoryEntry(step="context", agent="ctx", summary="Collected"),
            ],
        )
        state = build_state(prev, step="", agent="")
        self.assertEqual(state.step, "context")
        self.assertEqual(state.agent, "ctx")

    def test_build_state_explicit_step_wins_over_fallback(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
            summary="Analyzed request",
        )
        state = build_state(prev, step="context", agent="ctx")
        self.assertEqual(state.step, "context")
        self.assertEqual(state.agent, "ctx")

    def test_build_state_initial_state_unchanged(self):
        """No prev state, empty args — do not fabricate a step at init."""
        state = build_state(None, step="", agent="")
        self.assertEqual(state.step, "")
        self.assertEqual(state.agent, "")

    def test_build_state_falls_back_when_prev_step_empty_but_history_set(self):
        """prev.step empty but history non-empty → last history entry wins even
        for the agent field (agent not in history order, use prev.agent)."""
        prev = WorkflowState(
            version=1,
            workflow="default",
            step="",
            agent="",
            history=[HistoryEntry(step="analyze", agent="genius", summary="A")],
        )
        state = build_state(prev, step="", agent="")
        self.assertEqual(state.step, "analyze")
        self.assertEqual(state.agent, "genius")


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


class TestCollapseSections(unittest.TestCase):
    """Test the generic collapsible-section transform (issue #557)."""

    HEADINGS = ["FILES FOUND", "RELEVANT SNIPPETS", "EXTERNAL REFERENCES", "OTHER FILES"]

    def test_wraps_each_section_with_details(self):
        body = (
            "## Title\n\n"
            "Exec summary.\n\n"
            "### FILES FOUND\n"
            "*12 files covering the posting pipeline.*\n"
            "| a.py | 1-10 |\n\n"
            "### RELEVANT SNIPPETS\n"
            "_No snippets._\n"
            "```python\n"
            "def foo():\n"
            "    return 42\n"
            "```\n\n"
            "### EXTERNAL REFERENCES\n"
            "Some links.\n\n"
            "### OTHER FILES\n"
            "Nothing else.\n"
        )
        out = collapse_sections(body, self.HEADINGS)
        self.assertEqual(out.count("<details>"), 4)
        self.assertEqual(out.count("</details>"), 4)
        # Title + exec summary stay visible outside any details block
        self.assertTrue(out.startswith("## Title\n\nExec summary.\n\n"))

    def test_summary_carries_resume_when_present(self):
        body = "### FILES FOUND\n*12 files covering the posting pipeline.*\n| a.py | 1-10 |\n"
        out = collapse_sections(body, self.HEADINGS)
        self.assertIn(
            "<summary>FILES FOUND — 12 files covering the posting pipeline.</summary>", out
        )

    def test_summary_without_resume_is_bare_heading(self):
        body = "### EXTERNAL REFERENCES\nSome links.\n"
        out = collapse_sections(body, self.HEADINGS)
        self.assertIn("<summary>EXTERNAL REFERENCES</summary>", out)

    def test_single_section_and_no_match_pass_through(self):
        body = "### UNRELATED\ncontent\n"
        self.assertEqual(collapse_sections(body, self.HEADINGS), body)
        self.assertEqual(collapse_sections("", self.HEADINGS), "")
        self.assertEqual(collapse_sections(body, []), body)

    def test_section_boundary_respects_nested_headings_and_fences(self):
        body = (
            "### FILES FOUND\n"
            "intro\n"
            "#### sub detail\n"
            "more\n"
            "```\n"
            "### NOT A REAL HEADING inside fence\n"
            "```\n"
            "tail\n"
        )
        out = collapse_sections(body, ["FILES FOUND"])
        self.assertEqual(out.count("<details>"), 1)
        for token in ("#### sub detail", "### NOT A REAL HEADING inside fence", "tail"):
            self.assertIn(token, out)

    def test_content_byte_identical_when_unwrapped(self):
        body = (
            "### FILES FOUND\n*resume.*\ncontent line\n\n"
            "### OTHER FILES\nother content\n"
        )
        out = collapse_sections(body, self.HEADINGS)
        # Unwrap the <details>/<summary> markup; the wrapping inserts blank
        # lines, so normalize runs of newlines before comparing.
        unwrapped = re.sub(r"</?details>|<summary>.*?</summary>", "", out)
        normalized = lambda text: re.sub(r"\n{2,}", "\n\n", text).strip()
        self.assertEqual(normalized(unwrapped), normalized(body))


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

    def test_roundtrip_preserves_parent_workflow_fields(self):
        """parent_workflow / parent_workflow_step survive serialize → deserialize."""
        original = WorkflowState(
            workflow="child",
            step="impl",
            agent="dev",
            version=2,
            phase="execution",
            parent_workflow="parent",
            parent_workflow_step="subtask",
        )

        comment = to_git_comment(original)
        restored = extract_state_from_body(comment)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.parent_workflow, "parent")
        self.assertEqual(restored.parent_workflow_step, "subtask")
        self.assertEqual(restored.workflow, "child")
        self.assertEqual(restored.step, "impl")

    def test_roundtrip_preserves_scaffold_branch(self):
        """scaffold_branch survives serialize → deserialize (issue #604)."""
        original = WorkflowState(
            workflow="plan-orchestration",
            step="plan",
            agent="genius",
            version=3,
            scaffold_branch="feature/issue-604-scaffold-branch-fetch",
        )

        comment = to_git_comment(original)
        restored = extract_state_from_body(comment)

        self.assertIsNotNone(restored)
        self.assertEqual(
            restored.scaffold_branch, "feature/issue-604-scaffold-branch-fetch"
        )
        self.assertEqual(restored.workflow, "plan-orchestration")

    def test_roundtrip_omits_absent_scaffold_branch(self):
        """States without a scaffold_branch omit the key and restore as None."""
        original = WorkflowState(
            workflow="default",
            step="analyze",
            agent="genius",
        )

        comment = to_git_comment(original)
        restored = extract_state_from_body(comment)

        self.assertIsNotNone(restored)
        self.assertIsNone(restored.scaffold_branch)

    def test_roundtrip_preserves_parent_block(self):
        """The parent pointer survives serialize → deserialize (issue #613)."""
        original = WorkflowState(
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            version=3,
            parent={
                "workflow": "plan-orchestration",
                "step": "sub_impl",
                "issue_number": 589,
                "plan_step_index": 2,
                "plan_total_steps": 6,
            },
        )

        comment = to_git_comment(original)
        restored = extract_state_from_body(comment)

        self.assertIsNotNone(restored)
        self.assertIsNotNone(restored.parent)
        self.assertEqual(restored.parent.workflow, "plan-orchestration")
        self.assertEqual(restored.parent.step, "sub_impl")
        self.assertEqual(restored.parent.issue_number, 589)
        self.assertEqual(restored.parent.plan_step_index, 2)
        self.assertEqual(restored.parent.plan_total_steps, 6)

    def test_roundtrip_omits_absent_parent(self):
        """States without a parent block omit the key and restore as None."""
        original = WorkflowState(
            workflow="default",
            step="analyze",
            agent="genius",
        )

        comment = to_git_comment(original)
        restored = extract_state_from_body(comment)

        self.assertIsNotNone(restored)
        self.assertIsNone(restored.parent)


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

    def test_malformed_latest_state_falls_through_to_valid_earlier(self):
        """When the newest comment has state markers but malformed JSON,
        extract_state should skip it and find the earlier valid state."""
        body_valid = self._make_state_body(version=3)
        body_malformed = (
            "<!-- WORKFLOW_STATE_START -->\n"
            "{\"invalid json\n"
            "<!-- WORKFLOW_STATE_END -->"
        )
        comments = [
            {"id": 100, "body": body_valid},
            {"id": 200, "body": body_malformed},  # Newest — malformed
        ]
        state = extract_state(comments)
        self.assertIsNotNone(state)
        self.assertEqual(state.version, 3)


class TestIssueInfo(unittest.TestCase):
    """Test IssueInfo model and build_state() issue persistence."""

    def test_issue_info_creation(self):
        info = IssueInfo(
            number=42,
            url="https://github.com/repo/name/issues/42",
            title="Test issue",
            author="testuser",
            repo="repo/name",
        )
        self.assertEqual(info.number, 42)
        self.assertEqual(info.url, "https://github.com/repo/name/issues/42")
        self.assertEqual(info.title, "Test issue")
        self.assertEqual(info.author, "testuser")
        self.assertEqual(info.repo, "repo/name")

    def test_issue_in_workflow_state(self):
        info = IssueInfo(number=1, url="http://example.com/1", title="T", author="A", repo="R")
        state = WorkflowState(workflow="test", step="s", agent="a", issue=info)
        self.assertIsNotNone(state.issue)
        self.assertEqual(state.issue.number, 1)

    def test_issue_defaults_to_none(self):
        state = WorkflowState(workflow="test", step="s", agent="a")
        self.assertIsNone(state.issue)

    def test_build_state_sets_issue_on_initial(self):
        info = IssueInfo(number=7, url="http://u/7", title="Title", author="Auth", repo="Repo")
        state = build_state(None, workflow="w", step="s", agent="a", issue=info)
        self.assertIsNotNone(state.issue)
        self.assertEqual(state.issue.number, 7)

    def test_build_state_preserves_issue_across_transitions(self):
        info = IssueInfo(number=99, url="http://u/99", title="T", author="A", repo="R")
        s1 = build_state(None, workflow="w", step="analyze", agent="genius", issue=info)
        s2 = build_state(s1, workflow="w", step="implement", agent="dev")
        self.assertIsNotNone(s2.issue)
        self.assertEqual(s2.issue.number, 99)

    def test_build_state_overrides_issue_if_provided(self):
        info1 = IssueInfo(number=1, url="http://u/1", title="T1", author="A1", repo="R1")
        info2 = IssueInfo(number=2, url="http://u/2", title="T2", author="A2", repo="R2")
        s1 = build_state(None, workflow="w", step="s", agent="a", issue=info1)
        s2 = build_state(s1, workflow="w", step="s2", agent="a2", issue=info2)
        self.assertEqual(s2.issue.number, 2)

    def test_issue_roundtrip_via_json(self):
        info = IssueInfo(number=10, url="http://url", title="My Title", author="Author", repo="Owner/Repo")
        state = WorkflowState(workflow="w", step="s", agent="a", issue=info)
        json_str = state.model_dump_json(exclude_defaults=True)
        restored = WorkflowState.model_validate_json(json_str)
        self.assertIsNotNone(restored.issue)
        self.assertEqual(restored.issue.number, 10)
        self.assertEqual(restored.issue.author, "Author")


class TestPullRequestInfo(unittest.TestCase):
    """Test PullRequestInfo model and build_state() PR persistence."""

    def test_pr_info_creation(self):
        info = PullRequestInfo(
            number=42,
            url="https://github.com/repo/name/pull/42",
            title="Test PR",
            author="testuser",
            repo="repo/name",
        )
        self.assertEqual(info.number, 42)
        self.assertEqual(info.url, "https://github.com/repo/name/pull/42")
        self.assertEqual(info.title, "Test PR")
        self.assertEqual(info.author, "testuser")
        self.assertEqual(info.repo, "repo/name")

    def test_pr_in_workflow_state(self):
        info = PullRequestInfo(number=1, url="http://example.com/1", title="T", author="A", repo="R")
        state = WorkflowState(workflow="test", step="s", agent="a", pull_request=info)
        self.assertIsNotNone(state.pull_request)
        self.assertEqual(state.pull_request.number, 1)

    def test_pr_defaults_to_none(self):
        state = WorkflowState(workflow="test", step="s", agent="a")
        self.assertIsNone(state.pull_request)

    def test_build_state_sets_pr_on_initial(self):
        info = PullRequestInfo(number=7, url="http://u/7", title="Title", author="Auth", repo="Repo")
        state = build_state(None, workflow="w", step="s", agent="a", pull_request=info)
        self.assertIsNotNone(state.pull_request)
        self.assertEqual(state.pull_request.number, 7)

    def test_build_state_preserves_pr_across_transitions(self):
        info = PullRequestInfo(number=99, url="http://u/99", title="T", author="A", repo="R")
        s1 = build_state(None, workflow="w", step="analyze", agent="genius", pull_request=info)
        s2 = build_state(s1, workflow="w", step="implement", agent="dev")
        self.assertIsNotNone(s2.pull_request)
        self.assertEqual(s2.pull_request.number, 99)

    def test_build_state_overrides_pr_if_provided(self):
        info1 = PullRequestInfo(number=1, url="http://u/1", title="T1", author="A1", repo="R1")
        info2 = PullRequestInfo(number=2, url="http://u/2", title="T2", author="A2", repo="R2")
        s1 = build_state(None, workflow="w", step="s", agent="a", pull_request=info1)
        s2 = build_state(s1, workflow="w", step="s2", agent="a2", pull_request=info2)
        self.assertEqual(s2.pull_request.number, 2)

    def test_pr_roundtrip_via_json(self):
        info = PullRequestInfo(number=10, url="http://url", title="My Title", author="Author", repo="Owner/Repo")
        state = WorkflowState(workflow="w", step="s", agent="a", pull_request=info)
        json_str = state.model_dump_json(exclude_defaults=True)
        restored = WorkflowState.model_validate_json(json_str)
        self.assertIsNotNone(restored.pull_request)
        self.assertEqual(restored.pull_request.number, 10)
        self.assertEqual(restored.pull_request.author, "Author")


class TestDispatch(unittest.TestCase):
    """Tests for Dispatch model and integration with WorkflowState."""

    def test_dispatch_default_values(self):
        d = Dispatch()
        self.assertEqual(d.status, "advance")
        self.assertIsNone(d.task_hint)
        self.assertEqual(d.complexity, "medium")

    def test_dispatch_invalid_status_raises(self):
        with self.assertRaises(ValueError):
            Dispatch(status="invalid")

    def test_dispatch_all_statuses_valid(self):
        for status in ("advance", "loop", "hold", "terminate"):
            d = Dispatch(status=status)
            self.assertEqual(d.status, status)

    def test_dispatch_with_task_hint(self):
        d = Dispatch(status="advance", task_hint="dev")
        self.assertEqual(d.task_hint, "dev")

    def test_dispatch_with_sampling_params(self):
        d = Dispatch(
            status="advance", temperature=0.7, top_p=0.9, min_p=0.1,
            repetition_penalty=1.2, presence_penalty=0.5
        )
        self.assertEqual(d.temperature, 0.7)
        self.assertEqual(d.top_p, 0.9)

    def test_dispatch_in_workflow_state(self):
        state = WorkflowState(workflow="w", step="s", agent="a")
        self.assertIsNone(state.dispatch)
        state.dispatch = Dispatch(status="advance")
        self.assertEqual(state.dispatch.status, "advance")

    def test_dispatch_roundtrip_via_json(self):
        state = WorkflowState(workflow="w", step="s", agent="a")
        state.dispatch = Dispatch(status="loop", task_hint="dev", complexity="high")
        json_str = state.model_dump_json()
        restored = WorkflowState.model_validate_json(json_str)
        self.assertIsNotNone(restored.dispatch)
        self.assertEqual(restored.dispatch.status, "loop")
        self.assertEqual(restored.dispatch.task_hint, "dev")


class TestExtractDispatchFromState(unittest.TestCase):
    """Tests for extract_dispatch_from_state function."""

    def test_returns_none_when_no_state(self):
        self.assertIsNone(extract_dispatch_from_state("just some text"))

    def test_returns_none_when_no_dispatch(self):
        state = WorkflowState(workflow="w", step="s", agent="a")
        body = wrap_comment_with_state(state, "test comment")
        result = extract_dispatch_from_state(body)
        self.assertIsNone(result)

    def test_returns_dispatch_from_state(self):
        state = WorkflowState(workflow="w", step="s", agent="a")
        state.dispatch = Dispatch(status="advance", task_hint="dev")
        body = wrap_comment_with_state(state, "test comment")
        result = extract_dispatch_from_state(body)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.task_hint, "dev")

    def test_uses_prev_state_cache(self):
        state = WorkflowState(workflow="w", step="s", agent="a")
        state.dispatch = Dispatch(status="hold")
        result = extract_dispatch_from_state("irrelevant body", prev_state=state)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "hold")

    def test_prev_state_without_dispatch_returns_none(self):
        state = WorkflowState(workflow="w", step="s", agent="a")
        result = extract_dispatch_from_state("some body", prev_state=state)
        self.assertIsNone(result)


class TestDispatchSerialization(unittest.TestCase):
    """Test that dispatch data is properly visible in serialized JSON output.

    Validates the fix for issue #416: all-defaults Dispatch was producing
    "dispatch": {} in JSON, which looked like missing data.
    """

    def _serialize(self, state):
        """Serialize a state and return the parsed JSON dict."""
        json_str = wrap_comment_with_state(state, "")
        json_block = json_str.split("<!-- WORKFLOW_STATE_START -->")[1].split("<!-- WORKFLOW_STATE_END -->")[0]
        return json.loads(json_block.strip())

    def test_all_defaults_dispatch_vanishes(self):
        """All-defaults Dispatch should not appear in serialized JSON."""
        state = WorkflowState(workflow="w", step="s", agent="a")
        state.dispatch = Dispatch(status="advance", complexity="medium")
        data = self._serialize(state)
        self.assertNotIn("dispatch", data)

    def test_non_defaults_dispatch_preserved(self):
        """Non-default dispatch fields must appear in serialized JSON."""
        state = WorkflowState(workflow="w", step="s", agent="a")
        state.dispatch = Dispatch(status="loop", task_hint="dev", complexity="high")
        data = self._serialize(state)
        self.assertIn("dispatch", data)
        self.assertEqual(data["dispatch"]["status"], "loop")
        self.assertEqual(data["dispatch"]["task_hint"], "dev")
        self.assertEqual(data["dispatch"]["complexity"], "high")

    def test_no_dispatch_absent(self):
        """When dispatch is None, the key must not appear in JSON."""
        state = WorkflowState(workflow="w", step="s", agent="a")
        data = self._serialize(state)
        self.assertNotIn("dispatch", data)

    def test_dispatch_roundtrip_after_collapse(self):
        """Collapsed (absent) dispatch must round-trip correctly via comment body."""
        state = WorkflowState(workflow="w", step="s", agent="a")
        state.dispatch = Dispatch(status="advance")
        body = wrap_comment_with_state(state, "content")
        restored = extract_state_from_body(body)
        self.assertIsNotNone(restored)
        self.assertIsNone(restored.dispatch)

    def test_non_default_dispatch_roundtrip(self):
        """Non-default dispatch must round-trip through comment body intact."""
        state = WorkflowState(workflow="w", step="s", agent="a")
        state.dispatch = Dispatch(status="hold", reason="waiting on review")
        body = wrap_comment_with_state(state, "content")
        restored = extract_state_from_body(body)
        self.assertIsNotNone(restored)
        self.assertIsNotNone(restored.dispatch)
        self.assertEqual(restored.dispatch.status, "hold")
        self.assertEqual(restored.dispatch.reason, "waiting on review")


if __name__ == "__main__":
    unittest.main()
