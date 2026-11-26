"""Red tests for the engine-authored human-action notice (spec stub)."""

import pytest

from nebuko.engine.lib.config.sub_domains.human_action_notice import (
    KINDS,
    HumanActionNotice,
)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_notice_kind_is_immutable_value_object(kind):
    notice = HumanActionNotice(kind=kind, step_id="step-1")
    assert notice.kind == kind
    with pytest.raises(Exception):
        notice.kind = "workflow_complete"
