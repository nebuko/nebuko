"""The state's ``git`` sub-domain (issue #1153).

One structured block holding the single dev branch, the PR opened for it, and
one history entry per engine git action — the issue's suggested YAML sketch,
typed.
"""

from __future__ import annotations

from nebuko.engine.lib.workflow_state.sub_domains.git_info.git_info import (
    GitActionStatus,
    GitHistoryEntry,
    GitInfo,
)


class TestGitInfoModel:
    def test_defaults_are_empty(self):
        info = GitInfo()

        assert info.branch_name is None
        assert info.pr_number is None
        assert info.history == []

    def test_history_entry_defaults(self):
        entry = GitHistoryEntry(status=GitActionStatus.CREATE)

        assert entry.commit_id is None
        assert entry.turn == 0
        assert entry.detail == ""

    def test_statuses_are_the_engine_actions(self):
        assert [s.value for s in GitActionStatus] == [
            "create",
            "rebase",
            "conflict",
            "dirty",
            "pr",
            "already_applied",
        ]


class TestGitInfoRecord:
    def test_record_appends_one_entry(self):
        info = GitInfo()

        entry = info.record(GitActionStatus.CREATE, commit_id="abc1234", turn=1)

        assert info.history == [entry]
        assert entry.status is GitActionStatus.CREATE
        assert entry.commit_id == "abc1234"
        assert entry.turn == 1

    def test_record_appends_in_order(self):
        info = GitInfo()

        info.record(GitActionStatus.CREATE, turn=1)
        info.record(GitActionStatus.PR, turn=1, detail="PR opened")

        assert [e.status for e in info.history] == [
            GitActionStatus.CREATE,
            GitActionStatus.PR,
        ]
