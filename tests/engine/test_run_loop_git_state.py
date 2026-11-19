"""The engine's git lifecycle state wiring (issue #1153).

The engine records every git action it takes — branch create/continue/conflict,
the PR it opens, and the dirty-tree auto-commit — in the persisted state's
``git`` sub-domain, next to the legacy ``current_context["dev_branch"]``.
"""

from __future__ import annotations

from dataclasses import replace

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


def _issue_trigger(number: int = 590, title: str = "Fix it"):
    """An ``issue_comment`` trigger carrying the issue and repository payload."""
    from nebuko.engine.models import Trigger

    login = "olivier-norture"
    return Trigger(
        author=login,
        body="@@dev fix it",
        type="issue_comment",
        number=number,
        payload={
            "issue": {
                "number": number,
                "html_url": f"https://github.com/o/r/issues/{number}",
                "title": title,
                "user": {"login": login},
            },
            "repository": {"full_name": "o/r"},
            "comment": {"body": "@@dev fix it", "user": {"login": login}},
        },
    )


def _stub_flow(
    monkeypatch, root, *, scaffold=None, pr=None, finalize=None, trigger=None, env=None
):
    """A full engine.run() pass with the git machinery stubbed at the seams."""
    from nebuko.engine.run_loop import Engine

    engine = _engine_at_root(root.parent, root, trigger=trigger, env=env)
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

    def test_the_branch_is_recorded_in_the_git_block_only(
        self, tmp_path, monkeypatch, root
    ):
        # Issue #1397: ``git.branch_name`` is the single authority; the legacy
        # ``current_context["dev_branch"]`` duplicate is gone.
        engine = _stub_flow(monkeypatch, root)

        state = engine.adapter.load_state()
        assert state.git.branch_name == "feature/590-fix-it"
        assert "dev_branch" not in state.current_context
        assert state.git.base_branch == "main"
        assert state.git.created_at


class TestPullRequestRecorded:
    def test_the_pr_lands_in_the_pull_request_block(self, tmp_path, monkeypatch, root):
        engine = _stub_flow(monkeypatch, root)

        state = engine.adapter.load_state()
        assert state.pull_request is not None
        assert state.pull_request.number == 42
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


class TestPostedCommentCarriesEngineRecords:
    """The posted state comment is the next run's only state source (#1397).

    The agent child frames its artifact before the engine records the branch,
    the PR and the issue metadata, so the comment must be framed from the
    recorded state — otherwise everything below is invisible to the next run.
    """

    def _posted_state(self, engine):
        from nebuko.engine.lib.workflow_state import extract_state_from_body

        bodies = [entry.body for entry in engine.adapter.load_history()]
        for body in reversed(bodies):
            state = extract_state_from_body(body)
            if state is not None:
                return state
        raise AssertionError("no state-bearing comment was posted")

    def test_the_comment_carries_the_branch_the_pr_and_the_issue(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(
            monkeypatch, root, trigger=_issue_trigger(), env={"GITHUB_REPOSITORY": ""}
        )

        state = self._posted_state(engine)

        assert state.git is not None
        assert state.git.branch_name == "feature/590-fix-it"
        assert state.git.head_sha
        assert state.pull_request is not None and state.pull_request.number == 42
        assert state.issue is not None
        assert state.issue.number == 590
        assert state.issue.url == "https://github.com/o/r/issues/590"
        assert state.issue.title == "Fix it"
        assert state.issue.author == "olivier-norture"
        assert state.issue.repo == "o/r"

    def test_a_dispatch_targets_the_exported_repo_not_the_caller(
        self, tmp_path, monkeypatch, root
    ):
        base = _issue_trigger()
        trigger = replace(
            base,
            type="repository_dispatch",
            payload={
                **base.payload,
                "repository": {"full_name": "olivier-norture/nebuko-claude-code"},
                "client_payload": {"org_repo": "nebuko/nebuko", "issue_number": 590},
            },
        )
        engine = _stub_flow(
            monkeypatch,
            root,
            trigger=trigger,
            env={"REPO_NAME": "nebuko/nebuko", "GITHUB_REPOSITORY": "nebuko/nebuko"},
        )

        state = self._posted_state(engine)

        assert state.issue is not None
        assert state.issue.repo == "nebuko/nebuko"

    def test_a_trigger_without_an_issue_payload_records_no_issue(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(monkeypatch, root)

        state = self._posted_state(engine)

        assert state.issue is None
        assert state.git.branch_name == "feature/590-fix-it"


class TestGitStatePersists:
    def test_the_next_state_keeps_the_history(self):
        from nebuko.engine.lib.workflow_state import WorkflowState
        from nebuko.engine.lib.workflow_state.features.build_state.build_state import (
            build_state,
        )
        from nebuko.engine.lib.workflow_state.sub_domains.git_info.git_info import GitInfo

        git = GitInfo(branch_name="feature/590-fix-it", base_branch="main")
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


def _dispatch_trigger(number: int = 1388, body: str = "/nebuko run workflow"):
    """A ``repository_dispatch`` trigger: the target issue by number, no payload title."""
    from nebuko.engine.models import Trigger

    return Trigger(
        author="olivier-norture",
        body=body,
        type="repository_dispatch",
        number=number,
        payload={
            "client_payload": {"issue_number": number, "prompt_body": body},
            "repository": {"full_name": "olivier-norture/nebuko-claude-code"},
        },
    )


class TestDispatchScaffold:
    """A dispatch naming an issue scaffolds the same branch as a comment run (issue #1432)."""

    @pytest.fixture()
    def scaffolded(self, monkeypatch):
        calls: list[tuple[int, str]] = []

        def _scaffold(self):
            calls.append((self.issue_number, self.issue_title))
            return branch_scaffold.ScaffoldResult(
                ok=True, branch=branch_scaffold.feature_branch_name(self.issue_number, self.issue_title)
            )

        monkeypatch.setattr(branch_scaffold.BranchScaffold, "scaffold", _scaffold)
        return calls

    def _scaffold_for(self, tmp_path, root, monkeypatch, trigger, issue_title):
        import nebuko.engine.lib.github_client as github_client

        monkeypatch.setattr(
            github_client,
            "get_issue",
            lambda repo, number, **kw: {"title": issue_title} if issue_title else None,
        )
        from nebuko.engine.run_loop import Engine

        engine = _engine_at_root(
            tmp_path,
            root,
            trigger=trigger,
            env={"REPO_NAME": "nebuko/nebuko", "GITHUB_REPOSITORY": "nebuko/nebuko"},
        )
        monkeypatch.setattr(Engine, "_active_trigger", lambda self: trigger)
        monkeypatch.setattr(Engine, "_caller_root", lambda self: root)
        return engine._branch_scaffold()

    def test_the_fetched_issue_title_names_the_branch(
        self, tmp_path, monkeypatch, root, scaffolded
    ):
        result = self._scaffold_for(
            tmp_path, root, monkeypatch, _dispatch_trigger(), "Lane D: dispatch branch"
        )

        assert result.ok
        assert scaffolded == [(1388, "Lane D: dispatch branch")]

    def test_the_body_names_the_branch_when_the_issue_is_unreadable(
        self, tmp_path, monkeypatch, root, scaffolded
    ):
        self._scaffold_for(tmp_path, root, monkeypatch, _dispatch_trigger(), "")

        assert scaffolded == [(1388, "/nebuko run workflow")]

    def test_a_dispatch_without_an_issue_number_is_not_scaffolded(
        self, tmp_path, monkeypatch, root, scaffolded
    ):
        result = self._scaffold_for(
            tmp_path, root, monkeypatch, _dispatch_trigger(number=0), "Unused"
        )

        assert result.skipped
        assert scaffolded == []

    def test_an_empty_title_falls_back_to_the_issue_number(self):
        assert branch_scaffold.feature_branch_name(1388, "") == "feature/issue-1388"
