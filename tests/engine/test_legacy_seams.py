"""Spec-scenario tests for the additive seam refactors (issue #590, section 3.6).

Every seam test proves two things at once: the injected form works *and* the
legacy default stays intact (the GHA path must remain byte-identical).
"""

from __future__ import annotations

import pytest

from engine.models import CommentEntry, Trigger
from workflow_state.workflow_state import WorkflowState, to_git_comment

CREATED_AT = "2026-10-01T07:47:47Z"
BOT_AGENT_MARKER = "<!-- bot-agent-dev -->"


def _entry(body: str, author: str = "bot") -> CommentEntry:
    return CommentEntry(author=author, body=body, created_at=CREATED_AT)


def _state_body(step: str) -> str:
    return to_git_comment(WorkflowState(version=1, workflow="spec-driven-impl", step=step, agent="dev"))


class TestEventNormalizer:
    def test_issue_comment_payload_becomes_a_trigger(self):
        from event_normalizer import normalize_trigger

        trigger = normalize_trigger(
            {
                "comment": {"body": "@@dev fix it", "user": {"login": "olivier-norture"}},
                "issue": {"number": 590},
            }
        )

        assert isinstance(trigger, Trigger)
        assert (trigger.author, trigger.body, trigger.type, trigger.number) == (
            "olivier-norture",
            "@@dev fix it",
            "issue_comment",
            590,
        )

    def test_issue_payload_becomes_a_trigger(self):
        from event_normalizer import normalize_trigger

        trigger = normalize_trigger(
            {"issue": {"number": 590, "body": "make it so", "user": {"login": "olivier-norture"}}}
        )

        assert (trigger.author, trigger.body, trigger.type, trigger.number) == (
            "olivier-norture",
            "make it so",
            "issue",
            590,
        )


class TestFetchState:
    def test_extract_state_reads_the_latest_recorded_state(self):
        from resolve_config.features.fetch_state.fetch_state import extract_state

        state = extract_state([_entry(_state_body("analyze")), _entry(_state_body("stub"))])

        assert state is not None
        assert state.step == "stub"

    def test_extract_state_returns_none_without_a_state_block(self):
        from resolve_config.features.fetch_state.fetch_state import extract_state

        assert extract_state([_entry("just a comment")]) is None

    def test_fetch_state_uses_the_injected_history_provider(self):
        from resolve_config.features.fetch_state.fetch_state import fetch_state

        calls = []

        def provider():
            calls.append(True)
            return [_entry(_state_body("plan"))]

        state = fetch_state(provider=provider)

        assert calls == [True]
        assert state is not None and state.step == "plan"


class TestLoopGuardSeam:
    def test_detects_a_same_agent_loop(self):
        from loop_guard import check_loop_guard

        verdict = check_loop_guard(
            f"<!-- bot-workflow-terminal -->\n{BOT_AGENT_MARKER}\n@@dev continue",
            "dev",
        )

        assert verdict["SKIP_AGENT"] == "true"

    def test_allows_a_cross_agent_call(self):
        from loop_guard import check_loop_guard

        verdict = check_loop_guard(
            "<!-- bot-workflow-terminal -->\n<!-- bot-agent-genius -->\n@@dev implement",
            "dev",
        )

        assert verdict["SKIP_AGENT"] == "false"

    def test_accepts_injected_history(self):
        from loop_guard import check_loop_guard

        verdict = check_loop_guard("@@dev fix it", "dev", history=[_entry("history")])

        assert verdict["SKIP_AGENT"] == "false"


class TestCircuitBreakerSeam:
    # Each agent invocation leaves two bot markers (trigger + response), so
    # ``AGENT_CALLS == bot_markers // 2`` (see circuit_breaker.count_bot_markers).
    def test_trips_on_injected_history_at_the_threshold(self):
        from circuit_breaker import check_circuit_breaker

        history = [_entry(f"{BOT_AGENT_MARKER}\nintegration {index}") for index in range(6)]

        result = check_circuit_breaker(590, threshold=3, dry_run=True, history=history)

        assert result["CIRCUIT_BREAKER_TRIPPED"] == "true"
        assert result["AGENT_CALLS"] == "3"

    def test_stays_closed_below_the_threshold(self):
        from circuit_breaker import check_circuit_breaker

        result = check_circuit_breaker(
            590, threshold=8, dry_run=True, history=[_entry(f"{BOT_AGENT_MARKER}\nnothing")]
        )

        assert result["CIRCUIT_BREAKER_TRIPPED"] == "false"
        assert result["AGENT_CALLS"] == "0"

    def test_legacy_default_still_reads_the_github_api(self, monkeypatch):
        from circuit_breaker import check_circuit_breaker

        monkeypatch.setattr("circuit_breaker.get_issue_comments", lambda *a, **k: [])

        result = check_circuit_breaker(590, threshold=3)

        assert result["CIRCUIT_BREAKER_TRIPPED"] == "false"


class TestPrepareCommentSeam:
    def test_select_posted_body_prefers_the_latest_answer(self):
        from prepare_comment import select_posted_body

        assert select_posted_body("*Dev answer*", "finish note") == "*Dev answer*"

    def test_select_posted_body_falls_back_to_the_final_message(self):
        from prepare_comment import select_posted_body

        assert select_posted_body("", "finish note") == "finish note"

    def test_prepare_comment_hands_the_body_to_the_injected_poster(self, monkeypatch, tmp_path):
        from prepare_comment import prepare_comment

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TRIGGER_AUTHOR", raising=False)
        (tmp_path / "final_message.txt").write_text("*Dev answer*\n")

        posted = []

        def poster(body: str) -> str:
            posted.append(body)
            return "4242"

        assert prepare_comment(poster=poster) is True
        assert posted and "*Dev answer*" in posted[0]


class TestIssueCreationSeam:
    def test_create_sub_issues_uses_the_injected_creator(self):
        from issue_creation import SplitPlan, TaskDefinition, create_sub_issues

        calls = []

        def creator(title: str, body: str, labels: list) -> dict:
            calls.append((title, body, labels))
            return {"number": len(calls), "id": 100 + len(calls)}

        plan = SplitPlan(tasks=[TaskDefinition(title="task one", body="task one body")])

        created = create_sub_issues(None, plan, 590, create_issue=creator)

        assert [item["number"] for item in created] == [1]
        assert calls[0][0] == "[Sub-task 1/1] task one"
        assert calls[0][2] == ["parent-590"]

    @pytest.mark.parametrize("name", ["SplitPlan", "TaskDefinition", "CreateIssue"])
    def test_module_exposes_the_local_mode_contract(self, name):
        import issue_creation

        assert hasattr(issue_creation, name)
