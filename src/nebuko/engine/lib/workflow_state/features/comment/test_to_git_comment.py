import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config import brand
from workflow_state import WorkflowState, HistoryEntry, StateMetrics
from workflow_state.features.comment.to_git_comment import (
    format_collapsed_section,
    strip_state_section,
    wrap_comment_with_state,
)


def _body(section: str) -> str:
    return (
        "<details>\n"
        f"<summary>{section}</summary>\n"
        "\n"
        "```json\n"
        "<!-- WORKFLOW_STATE_START -->\n"
        "{}\n"
        "<!-- WORKFLOW_STATE_END -->\n"
        "```\n"
        "\n"
        "</details>\n"
    )


class TestFormatCollapsedSection(unittest.TestCase):
    """The state summary label is prefixed by the theme-adaptive brand mark."""

    def test_label_carries_the_brand_logo(self):
        state = WorkflowState(workflow="default", step="analyze", agent="architect")
        section = format_collapsed_section(state)
        self.assertIn(
            f"<summary>{brand().logo_mark()} Workflow State (v1)</summary>",
            section,
        )
        self.assertNotIn("🤖", section)

    def test_scalar_metrics_array_stays_on_one_line(self):
        """``agent_runtimes_per_call_ms`` must not be exploded across lines
        (issue #845)."""
        state = WorkflowState(
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            history=[
                HistoryEntry(
                    step="implement",
                    agent="dev",
                    metrics=StateMetrics(
                        agent_runtimes_per_call_ms=[1000.0, 2000.0, 3000.0]
                    ),
                )
            ],
        )
        section = format_collapsed_section(state)
        self.assertIn(
            '"agent_runtimes_per_call_ms": [1000.0, 2000.0, 3000.0]',
            section,
        )


class TestStripStateSection(unittest.TestCase):
    """Strip tolerates legacy labels, all brands, and bot routing markers."""

    def test_strips_legacy_emoji_labelled_section(self):
        body = _body("🤖 Workflow State (v1)") + "Human content"
        self.assertEqual(strip_state_section(body), "Human content")

    def test_strips_logo_labelled_section(self):
        body = _body(f"{brand().logo_mark()} Workflow State (v1)") + "Human content"
        self.assertEqual(strip_state_section(body), "Human content")

    def test_strips_bare_labelled_section(self):
        body = _body("Workflow State") + "Human content"
        self.assertEqual(strip_state_section(body), "Human content")

    def test_strips_marker_block_with_custom_summary_label(self):
        """Removal anchors on the state markers, not the human label (issue #844)."""
        body = _body("Totally Rebranded State") + "Human content"
        self.assertEqual(strip_state_section(body), "Human content")

    def test_strips_legacy_labelled_block_without_markers(self):
        """Label fallback still handles comments that predate the markers."""
        legacy = (
            "<details>\n"
            "<summary>🤖 Workflow State (v1)</summary>\n"
            "\n"
            "```json\n{}\n```\n"
            "\n"
            "</details>\n"
            "Human content"
        )
        self.assertEqual(strip_state_section(legacy), "Human content")

    def test_preserves_unrelated_details_block(self):
        body = _body("🤖 Workflow State (v1)") + "<details><summary>Other</summary>\nkept\n</details>"
        self.assertEqual(
            strip_state_section(body),
            "<details><summary>Other</summary>\nkept\n</details>",
        )

    def test_preserves_unrelated_blocks_around_the_state_block(self):
        body = (
            "<details><summary>Other</summary>\nkept\n</details>\n"
            + _body(f"{brand().logo_mark()} Workflow State (v2)")
            + "\nHuman content\n"
            "<details><summary>After</summary>\nkept too\n</details>"
        )

        stripped = strip_state_section(body)

        self.assertIn("<details><summary>Other</summary>\nkept\n</details>", stripped)
        self.assertIn("<details><summary>After</summary>\nkept too\n</details>", stripped)
        self.assertIn("Human content", stripped)
        self.assertNotIn("WORKFLOW_STATE", stripped)

    def test_removes_bot_routing_marker_lines(self):
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- bot-agent-architect -->\n"
            "<!-- workflow-continue -->\n"
            + _body(f"{brand().logo_mark()} Workflow State (v6)")
            + "\n*Architect answer*\n"
        )
        self.assertEqual(strip_state_section(body), "*Architect answer*")

    def test_removes_human_action_marker_line(self):
        body = "<!-- bot-workflow-terminal -->\n<!-- bot-human-action -->\nHuman content"
        self.assertEqual(strip_state_section(body), "Human content")

    def test_clean_body_is_unchanged(self):
        body = "Just a normal comment.\n\nWith a list:\n- one\n- two"
        self.assertEqual(strip_state_section(body), body)

    def test_round_trip_restores_body_for_both_generations(self):
        state = WorkflowState(workflow="default", step="analyze", agent="architect")
        body = "*Architect answer*"
        wrapped = wrap_comment_with_state(state, body)
        self.assertEqual(strip_state_section(wrapped), body)

        legacy = _body("🤖 Workflow State (v1)") + body
        self.assertEqual(strip_state_section(legacy), body)
