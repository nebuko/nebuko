"""Tests for the engine-owned HumanActionNotice sub-domain (issue #795).

The classification matrix and the rendered body are the contract that lets the
engine own every human ping: an agent reports its result and never mentions a
human, so this value object is the single place the ping is decided and built.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import HumanActionNotice


def _classify(
    status,
    reason=None,
    *,
    agent_ran=True,
    target="olivier-norture",
    step="plan",
    workflow="default",
):
    return HumanActionNotice.classify(
        status,
        reason,
        agent_ran=agent_ran,
        target=target,
        step=step,
        workflow=workflow,
    )


class TestClassifyMatrix:
    """Which finished runs owe a human a notification."""

    def test_human_gate_hold_is_an_approval_request(self):
        notice = _classify("hold", "human_gate:plan")
        assert notice is not None
        assert notice.kind == "awaiting_approval"

    def test_human_gate_prefix_matches_a_bare_marker(self):
        # The engine writes ``human_gate:<step>``; the ``startswith`` check also
        # accepts the bare marker so the classifier does not depend on the step
        # suffix being present.
        assert _classify("hold", "human_gate").kind == "awaiting_approval"

    def test_plain_hold_is_an_input_request(self):
        notice = _classify("hold", "no-action default: agent emitted no decision")
        assert notice.kind == "awaiting_input"

    def test_hold_without_a_reason_is_an_input_request(self):
        assert _classify("hold", None).kind == "awaiting_input"

    def test_terminate_is_workflow_complete(self):
        assert _classify("terminate", None).kind == "workflow_complete"

    def test_non_stop_statuses_never_ping(self):
        # advance/loop keep running on their own; soft_kill is a transient retry
        # (the exhausted-budget halt is a separate engine path).
        for status in ("advance", "loop", "soft_kill", ""):
            assert _classify(status, None) is None

    def test_soft_kill_is_not_a_stop(self):
        assert _classify("soft_kill", "gracefully timed out") is None

    def test_a_guard_or_terminal_skip_never_pings(self):
        # agent_ran=False means nothing was produced for a human to act on.
        assert _classify("hold", "human_gate:plan", agent_ran=False) is None
        assert _classify("terminate", None, agent_ran=False) is None

    def test_an_unresolvable_target_never_pings(self):
        assert _classify("hold", "human_gate:plan", target="") is None

    def test_records_the_stop_fields_verbatim(self):
        notice = _classify("hold", "human_gate:plan", step="plan", workflow="default")
        assert (notice.step, notice.workflow, notice.reason) == (
            "plan",
            "default",
            "human_gate:plan",
        )

    def test_strips_surrounding_whitespace_from_the_status(self):
        assert _classify(" hold ", "human_gate:plan").kind == "awaiting_approval"


class TestRender:
    """The rendered body is the single canonical human ping."""

    def test_render_is_headed_by_the_bot_and_human_action_markers(self):
        lines = _classify("hold", "human_gate:plan").render().splitlines()
        assert lines[0] == "<!-- bot-workflow-terminal -->"
        assert lines[1] == "<!-- bot-human-action -->"

    def test_render_pings_the_target_exactly_once(self):
        body = _classify("hold", "human_gate:plan").render()
        assert body.count("@olivier-norture") == 1
        assert "@olivier-norture workflow **default**" in body

    def test_approval_body_names_the_step_and_the_resume_command(self):
        body = _classify("hold", "human_gate:plan", step="plan").render()
        assert "waiting for your approval" in body
        assert "step `plan`" in body
        assert "/nebuko resume" in body

    def test_input_body_asks_for_feedback(self):
        body = _classify("hold", "await something", step="context").render()
        assert "paused at step `context`" in body
        assert "/nebuko resume <your feedback>" in body

    def test_complete_body_announces_the_finish(self):
        body = _classify("terminate", None, step="review").render()
        assert "has finished at step `review`" in body

    def test_reason_is_appended_when_present(self):
        body = _classify("hold", "human_gate:plan").render()
        assert "_human_gate:plan_" in body

    def test_no_reason_line_when_absent(self):
        body = _classify("terminate", None).render()
        assert not body.rstrip().endswith("_")

    def test_reason_cannot_smuggle_a_live_human_ping(self):
        """The agent-authored reason is neutralized: only ``target`` pings (#795)."""
        body = _classify("hold", "blocked, cc @someone").render()
        assert "@\u200bsomeone" in body
        assert "@someone" not in body
        # The engine's own ping is the single live mention in the body.
        assert body.count("@olivier-norture") == 1

    def test_reason_mention_in_code_span_stays_readable(self):
        """The neutralizer preserves code spans (GitHub does not notify there)."""
        body = _classify("hold", "see `@dataclass` cc @someone").render()
        assert "`@dataclass`" in body
        assert "@\u200bsomeone" in body


class TestValueObject:
    """The notice is a frozen payload, like AgentStartNotice."""

    def test_is_immutable(self):
        notice = _classify("hold", "human_gate:plan")
        try:
            notice.kind = "workflow_complete"
        except Exception as exc:  # pydantic raises ValidationError on frozen models
            assert "frozen" in str(exc).lower()
        else:  # pragma: no cover - frozen models never reach this branch
            raise AssertionError("HumanActionNotice should be immutable")

    def test_round_trips_through_model_dump(self):
        notice = _classify("hold", "human_gate:plan")
        assert HumanActionNotice.model_validate(notice.model_dump()) == notice
