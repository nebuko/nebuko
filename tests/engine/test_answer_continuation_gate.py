"""Regression guard: a split answer triggers the engine once (issue #1002).

Issue #994 reported the duplicate: one logical context answer was posted as two
comments (``*(Part 1/2)*`` / ``*(Part 2/2)*``), each carrying the bot marker
footer *and* the ``<!-- workflow-continue -->`` token, and each fired its own
Nebuko run — one answer, two architect runs.

Three independent guarantees are pinned here:

* the entry-point gate (``run-task.if``) rejects any body stamped
  ``<!-- bot-answer-continuation -->``;
* the token literal is the same on the producer (Python) and consumer (YAML)
  side;
* the engine resolver treats a continuation body as inert even if the gate ever
  lets one through.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from resolve_config import resolve_config
from resolve_workflow import TASK_TO_PROMPT

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"

# The verbatim part-2 comment from issue #994: the bot/agent footer, the
# continuation header, and the trigger payload that fired the second run.
PART_2_OF_2 = (
    "<!-- bot-workflow-terminal -->\n"
    "<!-- bot-agent-context -->\n"
    "<!-- workflow-continue -->\n"
    "<!-- bot-answer-continuation -->\n"
    "*(Part 2/2)*\n\n"
    "## 🔍 Context Collection Report\n\n"
    "Continuation content.\n\n"
    "<!-- bot-trigger: architect -->\n"
)


def _answer_continuation_token() -> str:
    from prepare_comment import ANSWER_CONTINUATION_TOKEN

    return ANSWER_CONTINUATION_TOKEN


def _run_task_if() -> str:
    workflow = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    return workflow["jobs"]["run-task"]["if"]


@pytest.fixture
def prompt_base(tmp_path: Path) -> str:
    """Materialize the prompt filenames ``resolve_config`` looks up."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in TASK_TO_PROMPT.values():
        (prompts / name).write_text(f"# Prompt for {name}")
    return str(prompts)


class TestGateRejectsContinuationParts:
    def test_run_task_if_carries_the_negative_continuation_arm(self):
        """The issue_comment gate must refuse a continuation part (issue #1002)."""
        expression = _run_task_if()

        assert (
            f"!contains(github.event.comment.body, '{_answer_continuation_token()}')"
            in expression
        )

    def test_the_gate_literal_matches_the_python_token(self):
        """One literal, two languages: the producer and the gate must agree."""
        from resolve_config.features.parse_trigger.parse_trigger import (
            ANSWER_CONTINUATION_MARKER,
        )

        assert _answer_continuation_token() == ANSWER_CONTINUATION_MARKER
        assert _answer_continuation_token() in _run_task_if()


class TestContinuationBodyIsInertInTheEngine:
    def test_the_captured_part_2_comment_routes_no_agent(self, prompt_base: str):
        """Replay of issue #994's second part: no agent may be selected."""
        result = resolve_config(
            PART_2_OF_2, prompt_base, str(REPO_ROOT / "defaults" / "workflows"),
            is_bot=True,
        )

        assert result["SKIP_AGENT"] == "true"
        assert "AGENT_SELECTED" not in result or result["AGENT_SELECTED"] != "true"
        assert "continuation" in result["REASON"].lower()

    def test_a_continuation_body_with_a_machine_trigger_token_is_still_inert(
        self, prompt_base: str
    ):
        """The ``bot-trigger:`` arm of the gate must not rescue a continuation."""
        from resolve_config.features.parse_trigger.parse_trigger import (
            extract_machine_trigger,
            is_answer_continuation,
        )

        body = f"{_answer_continuation_token()}\n<!-- bot-trigger: architect -->\n"

        assert is_answer_continuation(body)
        assert extract_machine_trigger(body) == "architect"
        result = resolve_config(
            body, prompt_base, str(REPO_ROOT / "defaults" / "workflows"), is_bot=True
        )
        assert result["SKIP_AGENT"] == "true"

    def test_the_state_bearing_first_part_still_routes(self, prompt_base: str):
        """The guard must not neutralize part 1 — the run that owns the payload."""
        from resolve_config.features.parse_trigger.parse_trigger import (
            is_answer_continuation,
        )

        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- bot-agent-dev -->\n"
            "<!-- workflow-continue -->\n"
            "*(Part 1/2)*\n\n"
            "## Dev answer\n"
        )

        assert not is_answer_continuation(body)
        result = resolve_config(
            body, prompt_base, str(REPO_ROOT / "defaults" / "workflows"), is_bot=True
        )
        assert result.get("SKIP_AGENT") != "true" or "continuation" not in result.get(
            "REASON", ""
        ).lower()
