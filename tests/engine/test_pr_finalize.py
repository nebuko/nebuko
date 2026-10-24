"""PR finalizer: the session ends with an open pull request.

The ``git-pr-workflow`` skill used to teach the agent the PR mechanics (issue
#1138); the engine now owns them by driving the deterministic ``ensure_pr``
flow. These tests pin the finalizer's contract: the agent's own PR wins, the
``PR_*`` env vars the state builder reads are exported, and a failure is
returned as data — never raised.
"""

from __future__ import annotations

import subprocess

import pytest

from nebuko.engine.pr_finalize import PR_ENV_VARS, PrFinalizer, PrFinalizeResult


@pytest.fixture
def checkout(tmp_path):
    """A minimal git checkout (the finalizer never runs git itself)."""
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    return path


def _finalizer(checkout, env=None, **kwargs) -> PrFinalizer:
    return PrFinalizer(checkout, env or {}, **kwargs)


def _ensure_pr(monkeypatch, *, error=False, skipped=False, pr_url="", message="ok"):
    """Pin ``ensure_pr`` to a fixed outcome; returns the calls it received."""
    from agent_script.features.ensure_pr import ensure_pr as module

    calls: list[bool] = []

    class _Result:
        pass

    result = _Result()
    result.error = error
    result.skipped = skipped
    result.pr_url = pr_url
    result.message = message
    monkeypatch.setattr(
        module, "ensure_pr", lambda dry_run=False: calls.append(dry_run) or result
    )
    return calls


# ── Finalize ─────────────────────────────────────────────────────


def test_finalize_records_the_pr_and_exports_the_env(checkout, monkeypatch):
    calls = _ensure_pr(monkeypatch, pr_url="https://github.com/o/r/pull/42")
    env = {"GITHUB_REPOSITORY": "o/r"}
    monkeypatch.setenv("GITHUB_ACTOR", "someone")

    result = _finalizer(checkout, env, issue_number=1138, issue_title="T").finalize()

    assert calls == [False]
    assert result.ok and not result.skipped
    assert result.pr_url == "https://github.com/o/r/pull/42"
    assert result.pr_number == 42
    assert result.pr_title == "T"
    import os

    assert os.environ["PR_NUMBER"] == "42"
    assert os.environ["PR_URL"] == "https://github.com/o/r/pull/42"
    assert os.environ["PR_TITLE"] == "T"
    assert os.environ["PR_AUTHOR"] == "someone"


def test_finalize_prefers_the_issue_title_for_the_pr_title(checkout, monkeypatch):
    _ensure_pr(monkeypatch, pr_url="https://github.com/o/r/pull/7")

    result = _finalizer(checkout, {}, issue_number=7, issue_title="The title").finalize()

    assert result.ok
    assert result.pr_title == "The title"


def test_finalize_falls_back_to_a_generic_title(checkout, monkeypatch):
    _ensure_pr(monkeypatch, pr_url="https://github.com/o/r/pull/7")

    result = _finalizer(checkout, {}, issue_number=7).finalize()

    assert result.ok
    assert result.pr_title == "Work for issue #7"


def test_finalize_reports_an_error_as_data(checkout, monkeypatch):
    _ensure_pr(monkeypatch, error=True, message="error: boom")

    result = _finalizer(checkout, {}).finalize()

    assert not result.ok
    assert "boom" in result.detail


def test_finalize_skips_when_ensure_pr_skips(checkout, monkeypatch):
    _ensure_pr(monkeypatch, skipped=True, message="detached HEAD")

    result = _finalizer(checkout, {}).finalize()

    assert result.ok and result.skipped
    assert "detached HEAD" in result.detail


def test_finalize_skips_when_there_is_no_pr_to_record(checkout, monkeypatch):
    """A workflow-only delta publishes as a patch, not a PR (issue #1080)."""
    _ensure_pr(monkeypatch, message="workflow patch delivered")

    result = _finalizer(checkout, {}).finalize()

    assert result.ok and result.skipped
    assert "no PR" in result.detail


def test_pr_env_vars_are_the_ones_the_state_builder_reads():
    assert PR_ENV_VARS == ("PR_NUMBER", "PR_URL", "PR_TITLE", "PR_AUTHOR")
