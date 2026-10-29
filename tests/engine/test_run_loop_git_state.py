"""Red tests for the engine's git lifecycle state wiring (issue #1153).

The engine records every git action it takes — branch create/rebase/conflict,
the PR it opens, and the dirty-tree auto-commit — in the persisted state's
``git`` sub-domain, next to the legacy ``current_context["dev_branch"]``.
"""

from __future__ import annotations

import pytest

from nebuko.engine.lib.git import branch_scaffold, git_session, pr_finalize
from nebuko.engine.lib.git.git_session import GuardResult
from nebuko.engine.lib.workflow_state.sub_domains.git_info.git_info import GitActionStatus

from tests.engine.test_run_loop import (
    _agent_result,
    _engine_at_root,
    _resolved_with_hooks,
    _stub_resolved,
    _wiring_state,
)


def _stub_flow(monkeypatch, root, *, scaffold=None, pr=None, finalize=None):
    """A full engine.run() pass with the git machinery stubbed at the seams."""
    from nebuko.engine.run_loop import Engine

    engine = _engine_at_root(root.parent, root)
    _stub_resolved(monkeypatch, _resolved_with_hooks())

    def _scaffold_stub(self):
        result = scaffold or branch_scaffold.ScaffoldResult(
            ok=True, branch="feature/590-fix-it"
        )
        self._scaffold_state["result"] = result
        return result

    monkeypatch.setattr(Engine, "_branch_scaffold", _scaffold_stub)
    monkeypatch.setattr(
        Engine,
        "_git_session_preflight",
        lambda self: git_session.GuardResult(ok=True),
    )
    monkeypatch.setattr(
        Engine,
        "_pr_finalize",
        lambda self, agent_result=None: pr
        or pr_finalize.PrFinalizeResult(
            ok=True,
            detail="PR opened",
            pr_url="https://github.com/o/r/pull/42",
        ),
    )
    monkeypatch.setattr(
        Engine,
        "_git_session_finalize",
        lambda self: finalize or GuardResult(ok=True),
    )
    monkeypatch.setattr(
        Engine,
        "invoke_agent",
        lambda self, prompt: {**_agent_result("done"), "state": _wiring_state()},
    )
    engine.run()
    return engine


@pytest.fixture()
def root(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return path


class TestScaffoldBranchRecorded:
    def test_a_created_branch_is_recorded_as_create(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(
            monkeypatch,
            root,
            scaffold=branch_scaffold.ScaffoldResult(
                ok=True, created=True, branch="feature/590-fix-it"
            ),
        )

        state = engine.adapter.load_state()
        assert state.git.branch_name == "feature/590-fix-it"
        assert GitActionStatus.CREATE in [e.status for e in state.git.history]

    def test_a_rebased_branch_is_recorded_as_rebase(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(
            monkeypatch,
            root,
            scaffold=branch_scaffold.ScaffoldResult(
                ok=True, created=False, branch="feature/590-fix-it"
            ),
        )

        state = engine.adapter.load_state()
        assert state.git.branch_name == "feature/590-fix-it"
        assert GitActionStatus.REBASE in [e.status for e in state.git.history]

    def test_a_conflict_is_recorded_as_conflict(self, tmp_path, monkeypatch, root):
        engine = _stub_flow(
            monkeypatch,
            root,
            scaffold=branch_scaffold.ScaffoldResult(
                ok=True,
                conflict=True,
                branch="feature/590-fix-it",
                detail="rebase has conflicts in: base.txt",
                note="Resolve them as your FIRST action",
            ),
        )

        state = engine.adapter.load_state()
        assert state.git.branch_name == "feature/590-fix-it"
        assert GitActionStatus.CONFLICT in [e.status for e in state.git.history]

    def test_the_legacy_dev_branch_context_is_still_written(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(monkeypatch, root)

        state = engine.adapter.load_state()
        assert state.current_context.get("dev_branch") == "feature/590-fix-it"


class TestPullRequestRecorded:
    def test_the_pr_number_lands_in_the_git_block(self, tmp_path, monkeypatch, root):
        engine = _stub_flow(monkeypatch, root)

        state = engine.adapter.load_state()
        assert state.git.pr_number == 42
        assert GitActionStatus.PR in [e.status for e in state.git.history]


class TestDirtyFinalizeRecorded:
    def test_an_auto_commit_is_recorded_as_dirty_with_its_sha(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(
            monkeypatch,
            root,
            finalize=GuardResult(ok=True, detail="committed abc1234"),
        )

        state = engine.adapter.load_state()
        dirty = [e for e in state.git.history if e.status is GitActionStatus.DIRTY]
        assert len(dirty) == 1
        assert dirty[0].commit_id == "abc1234"

    def test_a_clean_finalize_records_no_dirty_entry(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(monkeypatch, root)

        state = engine.adapter.load_state()
        assert not [e for e in state.git.history if e.status is GitActionStatus.DIRTY]
