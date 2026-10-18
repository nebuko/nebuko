"""Regression guard for the dev↔review loop restart (issue #924).

The captured issue-#923 state ends on ``default/review`` with the review agent
having rejected the implementation: ``dispatch == {"reason": "round 2"}`` — a
human-readable round counter and *no* verdict key. ``default/review`` declares
``on_result`` edges (``approved``/``rejected``), so the router matched nothing,
resolved no successor, and the engine told the reporter the workflow was
complete instead of re-running ``dev``.

Two things are pinned here, both from the shipped configuration:

* the *cause* — the review instructions must put the verdict in the routing key
  (``dispatch_next(appreciation='rejected')``) and keep the round counter in
  free text (``note``); instructing the counter as the verdict is what produced
  the verdict-less capture;
* the *effect* — replaying the verbatim captured ``workflow_state`` through
  ``resolve_config()`` against ``defaults/workflows``: the captured blob does
  not route, the verdict the instructions now prescribe restarts ``dev`` at
  ``implement``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resolve_config import resolve_config
from resolve_workflow import TASK_TO_PROMPT
from workflow_state.workflow_state import STATE_MARKER_END, STATE_MARKER_START

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = REPO_ROOT / "defaults"
WORKFLOWS_DIR = DEFAULTS / "workflows"
REVIEW_PROMPT = DEFAULTS / "prompts" / "review.md"
PLAN_REVIEW_SKILL = DEFAULTS / "skills" / "plan-review.md"
CAPTURED_STATE = (
    REPO_ROOT
    / "src"
    / "nebuko"
    / "engine"
    / "lib"
    / "fixtures"
    / "workflow_state_issue923_review_round2.json"
)


@pytest.fixture
def prompt_base(tmp_path: Path) -> str:
    """Materialize the prompt filenames ``resolve_config`` looks up."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in TASK_TO_PROMPT.values():
        (prompts / name).write_text(f"# Prompt for {name}")
    return str(prompts)


def _replay(prompt_base: str, **outcome) -> dict:
    """Replay the captured state (verbatim, but for the turn outcome) end-to-end."""
    state = json.loads(CAPTURED_STATE.read_text())
    state["turn_outcome"] = outcome
    body = (
        "<!-- bot-agent-review -->\n"
        + STATE_MARKER_START + "\n"
        + json.dumps(state)
        + "\n" + STATE_MARKER_END + "\n"
    )
    return resolve_config(body, prompt_base, str(WORKFLOWS_DIR), is_bot=True)


class TestReviewInstructionsPrescribeTheVerdictKey:
    """The shipped review instructions must route on ``appreciation``."""

    def test_the_review_prompt_signals_rejection_through_the_routing_key(self):
        text = REVIEW_PROMPT.read_text()

        assert "appreciation='rejected'" in text
        assert "appreciation='approved'" in text

    def test_the_review_prompt_keeps_the_round_counter_out_of_routing(self):
        """``note`` is free text: a counter there is not a verdict (issue #924).

        The pre-fix prompt rode the rejection on a free-text round counter; that
        instruction is what produced the captured verdict-less dispatch. It must
        not come back, and no ``dispatch_next`` call may carry a ``reason``.
        """
        text = REVIEW_PROMPT.read_text()

        assert "put `round N` in `reason`" not in text
        assert [
            line
            for line in text.splitlines()
            if "dispatch_next" in line and "reason=" in line
        ] == []

    def test_the_plan_review_skill_signals_the_verdict_the_same_way(self):
        text = PLAN_REVIEW_SKILL.read_text()

        assert 'appreciation="rejected"' in text
        assert 'appreciation="approved"' in text


class TestCapturedRound2Replay:
    """Replay the captured state against the shipped ``default`` workflow."""

    def test_the_captured_verdictless_blob_routes_nowhere(self, prompt_base: str):
        """The reported dead-end: no verdict key means no routing decision.

        It must resolve as an *unresolved* skip — a config gap the engine pings
        the human about — never as a benign terminal that reads like a finish
        (issue #964).
        """
        result = _replay(
            prompt_base, status="incomplete", incomplete_reason="no_outcome",
            note="round 2",
        )

        assert result["SKIP_AGENT"] == "true"
        assert "SKIP_KIND" not in result
        # #978: a verdictless turn is a notification, so the reason names the
        # missing outcome instead of a missing successor � still an unresolved
        # skip the engine pings a human about, never a terminal finish.
        assert "no verdict" in result["REASON"]

    def test_the_rejected_verdict_restarts_dev_at_implement(self, prompt_base: str):
        """With the verdict the instructions now prescribe, ``dev`` restarts."""
        result = _replay(
            prompt_base, status="completed", appreciation="rejected", round=2,
        )

        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == "default"
        assert result["WORKFLOW_STEP"] == "implement"

    def test_the_approved_verdict_ends_the_workflow(self, prompt_base: str):
        """The terminal ``approved`` edge stays the honest, agent-free end."""
        result = _replay(
            prompt_base, status="completed", appreciation="approved"
        )

        assert result["SKIP_AGENT"] == "true"
