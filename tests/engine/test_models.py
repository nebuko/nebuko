"""Spec-scenario tests for the engine vocabulary (issue #590, plan section 3.1)."""

from __future__ import annotations

import dataclasses

import pytest

from engine.models import CommentEntry, ExitCode, RoutingDecision, RunOutcome, Trigger


def _trigger(**overrides):
    values = {"author": "olivier-norture", "body": "@@dev fix it", "type": "issue_comment", "number": 590}
    values.update(overrides)
    return Trigger(**values)


class TestTrigger:
    def test_field_order_matches_the_frozen_contract(self):
        assert [f.name for f in dataclasses.fields(Trigger)] == [
            "author",
            "body",
            "type",
            "number",
            "payload",
        ]

    def test_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _trigger().author = "someone-else"

    def test_payload_defaults_to_empty_and_is_not_shared(self):
        assert _trigger().payload == {}
        assert _trigger().payload is not _trigger().payload

    def test_payload_accepts_the_raw_event(self):
        assert _trigger(payload={"action": "created"}).payload == {"action": "created"}


class TestCommentEntry:
    def test_carries_author_body_and_created_at(self):
        entry = CommentEntry(author="bot", body="hi", created_at="2026-10-01T07:47:47Z")
        assert (entry.author, entry.body, entry.created_at) == ("bot", "hi", "2026-10-01T07:47:47Z")

    def test_is_frozen(self):
        entry = CommentEntry(author="bot", body="hi", created_at="2026-10-01T07:47:47Z")
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.body = "changed"


class TestRoutingDecision:
    def test_defaults_describe_a_plain_advance(self):
        decision = RoutingDecision(status="advance")
        assert (decision.agent, decision.complexity, decision.branch, decision.reason) == (
            None,
            "medium",
            None,
            None,
        )

    @pytest.mark.parametrize("status", ["advance", "loop", "hold"])
    def test_accepts_the_three_documented_statuses(self, status):
        assert RoutingDecision(status=status).status == status


class TestExitCode:
    def test_values_distinguish_the_three_terminal_outcomes(self):
        assert ExitCode.ADVANCE == 0
        assert ExitCode.ERROR == 1
        assert ExitCode.HOLD == 10
        assert ExitCode.TERMINAL == 20

    def test_is_an_int_enum_so_it_can_be_returned_as_a_process_code(self):
        assert int(ExitCode.HOLD) == 10


class TestRunOutcome:
    def test_field_order_matches_the_frozen_contract(self):
        assert [f.name for f in dataclasses.fields(RunOutcome)] == [
            "exit_code",
            "routing",
            "prompt",
            "posted_body",
            "state",
            "phases",
        ]

    def test_carries_the_routing_decision_and_the_emitted_phases(self):
        outcome = RunOutcome(
            exit_code=ExitCode.ADVANCE,
            routing=RoutingDecision(status="advance"),
            prompt="prompt",
            posted_body="body",
            state=None,
            phases=("normalize",),
        )
        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.phases == ("normalize",)
