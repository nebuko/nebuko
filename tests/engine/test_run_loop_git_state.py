"""The engine's git lifecycle state wiring (issue #1153).

The engine records every git action it takes — branch create/continue/conflict,
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

    def test_a_continued_branch_is_recorded_as_continue(
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
        assert GitActionStatus.CONTINUE in [e.status for e in state.git.history]

    def test_a_branch_already_containing_the_base_is_recorded_as_already_applied(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(
            monkeypatch,
            root,
            scaffold=branch_scaffold.ScaffoldResult(
                ok=True,
                already_up_to_date=True,
                base_contained=True,
                branch="feature/590-fix-it",
            ),
        )

        state = engine.adapter.load_state()
        assert GitActionStatus.ALREADY_APPLIED in [e.status for e in state.git.history]

    def test_a_clean_probe_is_recorded_as_continue_not_already_applied(
        self, tmp_path, monkeypatch, root
    ):
        # A clean probe also sets already_up_to_date, but the base isn't in the
        # branch: it was deliberately left out (issue #1242).
        engine = _stub_flow(
            monkeypatch,
            root,
            scaffold=branch_scaffold.ScaffoldResult(
                ok=True,
                already_up_to_date=True,
                branch="feature/590-fix-it",
            ),
        )

        state = engine.adapter.load_state()
        assert [e.status for e in state.git.history][0] is GitActionStatus.CONTINUE

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
            finalize=GuardResult(ok=True, auto_commit="abc1234"),
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


class TestGitStatePersists:
    def test_the_next_state_keeps_the_history(self):
        from nebuko.engine.lib.workflow_state import WorkflowState
        from nebuko.engine.lib.workflow_state.features.build_state.build_state import (
            build_state,
        )
        from nebuko.engine.lib.workflow_state.sub_domains.git_info.git_info import GitInfo

        git = GitInfo(branch_name="feature/590-fix-it", pr_number=42)
        git.record(GitActionStatus.PR)
        prev = WorkflowState(run_id=1, workflow="wf", step="plan", agent="dev", git=git)

        state = build_state(prev, workflow="wf", step="implement", agent="dev")

        assert state.git == git

    def test_it_survives_the_state_comment_round_trip(self):
        from nebuko.engine.lib.workflow_state import WorkflowState, state_to_json
        from nebuko.engine.lib.workflow_state.sub_domains.git_info.git_info import GitInfo

        git = GitInfo(branch_name="feature/590-fix-it")
        git.record(GitActionStatus.CONTINUE, commit_id="abc1234", turn=2)
        state = WorkflowState(run_id=1, workflow="wf", step="plan", agent="dev", git=git)

        assert WorkflowState.model_validate_json(state_to_json(state)).git == git

    def test_a_state_without_git_still_loads(self):
        from nebuko.engine.lib.workflow_state import WorkflowState

        assert WorkflowState.model_validate({"run_id": 1}).git is None
