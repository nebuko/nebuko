"""Tests for the ThreadRecord ledger entity (issue #872)."""

import os
import sys

import pytest
from pydantic import ValidationError


from nebuko.engine.lib.workflow_state.sub_domains.thread_record import (  # noqa: E402
    ThreadRecord,
    ThreadRecordKind,
)


def _record(**overrides) -> ThreadRecord:
    payload = {
        "kind": ThreadRecordKind.AGENT_OUTPUT,
        "id": 6101088100,
        "url": "https://github.com/nebuko/nebuko/issues/869#issuecomment-6101088100",
        "user_id": 12345,
        "user_login": "olivier-norture",
        "created_at": "2026-10-11T04:36:02Z",
    }
    payload.update(overrides)
    return ThreadRecord(**payload)


class TestThreadRecordKind:
    def test_values_are_the_contract(self):
        assert ThreadRecordKind.ISSUE_BODY.value == "issue-body"
        assert ThreadRecordKind.COMMAND.value == "command"
        assert ThreadRecordKind.ENGINE_OUTPUT.value == "engine-output"
        assert ThreadRecordKind.AGENT_OUTPUT.value == "agent-output"
        assert ThreadRecordKind.HUMAN_FEEDBACK.value == "human-feedback"

    def test_members_are_unique(self):
        assert len({kind.value for kind in ThreadRecordKind}) == 5


class TestThreadRecord:
    def test_optional_fields_default_empty_and_untrusted(self):
        record = _record()
        assert record.user_name == ""
        assert record.author_association == ""
        assert record.trusted is False
        assert record.deleted is False

    def test_kind_accepts_its_string_value(self):
        assert _record(kind="agent-output").kind is ThreadRecordKind.AGENT_OUTPUT

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValidationError):
            _record(kind="chatter")

    def test_model_is_frozen(self):
        record = _record()
        with pytest.raises(ValidationError):
            record.trusted = True

    def test_round_trips_through_json(self):
        record = _record(
            kind=ThreadRecordKind.HUMAN_FEEDBACK,
            author_association="MEMBER",
            trusted=True,
            deleted=True,
        )
        dumped = record.model_dump(mode="json")
        assert dumped["kind"] == "human-feedback"
        assert dumped["trusted"] is True
        assert dumped["deleted"] is True
        assert ThreadRecord.model_validate(dumped) == record
