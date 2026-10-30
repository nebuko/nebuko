"""Tests for the persisted StepInputRecord entity (issue #872)."""

import os
import sys

import pytest
from pydantic import ValidationError


from nebuko.engine.lib.workflow_state.sub_domains.step_input_record import (
    StepInputRecord,
)
from nebuko.engine.lib.workflow_state.sub_domains.step_input_record.step_input_record import (
    MAX_VALUE_CHARS,
)


class TestStepInputRecord:
    def test_optional_fields_default(self):
        record = StepInputRecord(name="diff", binding="exec", ok=True)
        assert record.detail == ""
        assert record.value == ""
        assert record.truncated is False

    def test_name_is_required(self):
        with pytest.raises(ValidationError):
            StepInputRecord(binding="exec", ok=True)

    def test_failure_detail_is_kept(self):
        record = StepInputRecord(
            name="diff", binding="exec", ok=False, detail="exit 1"
        )
        assert record.ok is False
        assert record.detail == "exit 1"

    def test_model_is_frozen(self):
        record = StepInputRecord(name="diff", binding="exec", ok=True)
        with pytest.raises(ValidationError):
            record.value = "rewritten"

    def test_round_trips_through_json(self):
        record = StepInputRecord(
            name="context",
            binding="ref",
            ok=True,
            value="x" * 10,
            truncated=True,
        )
        assert StepInputRecord.model_validate(record.model_dump(mode="json")) == record

    def test_value_cap_is_a_positive_constant(self):
        assert MAX_VALUE_CHARS > 0
