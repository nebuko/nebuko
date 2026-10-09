import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config import brand
from workflow_state import WorkflowState
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


class TestStripStateSection(unittest.TestCase):
    """Strip tolerates the legacy emoji, the new logo, and bare labels."""

    def test_strips_legacy_emoji_labelled_section(self):
        body = _body("🤖 Workflow State (v1)") + "Human content"
        self.assertEqual(strip_state_section(body), "Human content")

    def test_strips_logo_labelled_section(self):
        body = _body(f"{brand().logo_mark()} Workflow State (v1)") + "Human content"
        self.assertEqual(strip_state_section(body), "Human content")

    def test_strips_bare_labelled_section(self):
        body = _body("Workflow State") + "Human content"
        self.assertEqual(strip_state_section(body), "Human content")

    def test_preserves_unrelated_details_block(self):
        body = _body("🤖 Workflow State (v1)") + "<details><summary>Other</summary>\nkept\n</details>"
        self.assertEqual(
            strip_state_section(body),
            "<details><summary>Other</summary>\nkept\n</details>",
        )

    def test_round_trip_restores_body_for_both_generations(self):
        state = WorkflowState(workflow="default", step="analyze", agent="architect")
        body = "*Architect answer*"
        wrapped = wrap_comment_with_state(state, body)
        self.assertEqual(strip_state_section(wrapped), body)

        legacy = _body("🤖 Workflow State (v1)") + body
        self.assertEqual(strip_state_section(legacy), body)
