"""The engine's persisted branch record through a full ``engine.run()``.

The scaffolded branch lands in ``current_context["dev_branch"]``. (Issue #1153
also sketched a typed ``state.git`` lifecycle record — create/rebase/conflict,
PR number, dirty auto-commit; only its model, ``workflow_state.sub_domains.
git_info``, was built. It is not wired into ``WorkflowState``, so its red
wiring tests were removed.)
"""

from __future__ import annotations

import pytest

from nebuko.engine.lib.git import branch_scaffold, git_session, pr_finalize
from nebuko.engine.lib.git.git_session import GuardResult

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


class TestDevBranchRecorded:
    def test_the_scaffolded_branch_is_written_to_dev_branch(
        self, tmp_path, monkeypatch, root
    ):
        engine = _stub_flow(monkeypatch, root)

        state = engine.adapter.load_state()
        assert state.current_context.get("dev_branch") == "feature/590-fix-it"
