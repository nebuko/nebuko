"""Shared --personal classification table (issue #1479).

Every sample in ``tests/fixtures/personal-command-samples.json`` must classify
the same way for the project side: a ``personal dispatch`` is skipped without
a run (condition ``start-noop``), a ``project run`` is not (``start-run``).
The rule is first-line-only, the same grammar as ``parse_command``.
"""

import json
from pathlib import Path

import pytest

from nebuko.engine.lib.resolve_config.sub_domains.workflow_command.workflow_command import (
    classify_comment,
    is_personal_dispatch,
    lane_flag_of,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "personal-command-samples.json"
SAMPLES = json.loads(FIXTURE.read_text(encoding="utf-8"))["samples"]


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["name"] for s in SAMPLES])
def test_sample_classification_matches_table(sample):
    dispatch = sample["class"] == "personal dispatch"
    assert is_personal_dispatch(sample["body"]) is dispatch
    assert (classify_comment(sample["body"]).kind == "personal_dispatch") is dispatch
    assert sample["condition"] == ("start-noop" if dispatch else "start-run")


def test_lane_flag_of_reads_only_the_first_line():
    assert lane_flag_of("thanks\n/nebuko approve gate --personal") is None
    assert lane_flag_of("/nebuko approve gate --project") == "project"
    assert lane_flag_of("/nebuko approve gate") is None


def test_lane_flag_after_feedback_is_feedback_text():
    assert lane_flag_of("/nebuko reject gate --feedback no --personal") is None
