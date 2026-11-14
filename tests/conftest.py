"""Test configuration for the engine suites.

``pyproject.toml`` sets ``pythonpath = ["src"]``, so ``nebuko`` — including the
``nebuko.engine.lib`` subpackages — is importable from the checkout with no
path setup here (issue #1165).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.paths import repo_root

_CHECKOUT = repo_root().resolve()

#: Engine phases that run git against the caller root: scaffold checks out a
#: branch, finalize commits leftovers and pushes, the PR finalizer pushes and
#: opens a PR.
_GIT_PHASES = (
    "_git_session_preflight",
    "_branch_scaffold",
    "_git_session_finalize",
    "_pr_finalize",
)


@pytest.fixture(autouse=True)
def _engine_git_phases_never_touch_this_checkout(monkeypatch):
    """Turn the engine's git phases into benign skips on the real checkout.

    Tests that build an ``Engine`` on ``repo_root()`` and run the whole loop
    (parity, phase isolation) would otherwise scaffold a feature branch in the
    developer's checkout, commit their uncommitted work and push it to
    GitHub (issue #1268: once the scaffold really checked the branch out, a
    local test run did exactly that). Tests of the git phases themselves run
    against a temporary repository and keep the real behavior.
    """
    from nebuko.engine import run_loop
    from nebuko.engine.lib.git import branch_scaffold, git_session, pr_finalize

    skips = {
        "_git_session_preflight": lambda: git_session.GuardResult(
            ok=True, skipped=True, detail="test: real checkout"
        ),
        "_branch_scaffold": lambda: branch_scaffold.ScaffoldResult(
            ok=True, skipped=True, detail="test: real checkout"
        ),
        "_git_session_finalize": lambda: git_session.GuardResult(
            ok=True, skipped=True, detail="test: real checkout"
        ),
        "_pr_finalize": lambda: pr_finalize.PrFinalizeResult(
            ok=True, skipped=True, detail="test: real checkout"
        ),
    }

    def _guarded(name):
        original = getattr(run_loop.Engine, name)

        def phase(self, *args, **kwargs):
            if Path(self._caller_root()).resolve() == _CHECKOUT:
                result = skips[name]()
                if name == "_branch_scaffold":
                    self._scaffold_state["result"] = result
                return result
            return original(self, *args, **kwargs)

        phase.__wrapped__ = original
        return phase

    for name in _GIT_PHASES:
        monkeypatch.setattr(run_loop.Engine, name, _guarded(name))
