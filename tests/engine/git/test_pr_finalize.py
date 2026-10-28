"""Tests for the engine-owned PR finalizer (issue #1138).

``ensure_pr`` is stubbed (monkeypatched at its module) so the tests exercise
only the finalizer's contract: success as data, error as data, benign skips,
and the PR-number parsing the engine's state recording relies on.
"""

from __future__ import annotations

import pytest

from agent_script.features.ensure_pr.ensure_pr import EnsurePrResult
from nebuko.engine.lib.git.pr_finalize import PrFinalizer, pr_number_from_url


@pytest.fixture()
def ensure_pr_result(monkeypatch):
    """Stub ``ensure_pr`` to return a canned result; returns the calls list."""
    calls: list[bool] = []
    holder: dict[str, EnsurePrResult | Exception] = {}

    def _fake(
        dry_run: bool = False, *, issue_title: str = "", interrupted: bool = False
    ) -> EnsurePrResult:
        calls.append(dry_run)
        holder["issue_title"] = issue_title
        outcome = holder.get("result")
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, EnsurePrResult)
        return outcome

    monkeypatch.setattr(
        "agent_script.features.ensure_pr.ensure_pr.ensure_pr", _fake
    )
    return holder, calls


def _finalize() -> object:
    return PrFinalizer().finalize()


class TestPrFinalizer:
    def test_success_returns_the_pr_as_data(self, ensure_pr_result):
        holder, calls = ensure_pr_result
        holder["result"] = EnsurePrResult(
            pr_url="https://github.com/nebuko/nebuko/pull/42",
            message="PR opened: https://github.com/nebuko/nebuko/pull/42",
        )

        result = _finalize()

        assert result.ok and not result.skipped
        assert result.pr_url == "https://github.com/nebuko/nebuko/pull/42"
        assert calls == [False]  # never a dry run: the PR must really open

    def test_an_existing_pr_is_returned_the_same_way(self, ensure_pr_result):
        # The agent's own PR wins: ensure_pr looks the branch's PR up first,
        # so the finalizer sees it as an ordinary success.
        holder, _ = ensure_pr_result
        holder["result"] = EnsurePrResult(
            pr_url="https://github.com/nebuko/nebuko/pull/7",
            message="PR already exists",
        )

        result = _finalize()

        assert result.ok
        assert result.pr_url == "https://github.com/nebuko/nebuko/pull/7"

    def test_an_error_is_data_not_an_exception(self, ensure_pr_result):
        holder, _ = ensure_pr_result
        holder["result"] = EnsurePrResult(error=True, message="error: push rejected")

        result = _finalize()

        assert not result.ok
        assert result.detail == "error: push rejected"
        assert result.pr_url == ""

    def test_a_crash_is_data_not_an_exception(self, ensure_pr_result):
        holder, _ = ensure_pr_result
        holder["result"] = RuntimeError("gh exploded")

        result = _finalize()

        assert not result.ok
        assert "gh exploded" in result.detail

    def test_a_benign_skip_stays_a_skip(self, ensure_pr_result):
        holder, _ = ensure_pr_result
        holder["result"] = EnsurePrResult(skipped=True, message="no git remote")

        result = _finalize()

        assert result.ok and result.skipped
        assert result.detail == "no git remote"

    def test_a_workflow_only_delta_is_a_skip_not_a_failure(self, ensure_pr_result):
        # A workflow-only delta publishes as a patch comment (issue #1080):
        # no PR URL, but the session is still fully published.
        holder, _ = ensure_pr_result
        holder["result"] = EnsurePrResult(message="workflow patch delivered")

        result = _finalize()

        assert result.ok and result.skipped
        assert result.pr_url == ""


class TestPrNumberFromUrl:
    def test_parses_the_number(self):
        assert pr_number_from_url("https://github.com/o/r/pull/42") == 42

    def test_tolerates_a_trailing_slash(self):
        assert pr_number_from_url("https://github.com/o/r/pull/42/") == 42

    def test_returns_none_for_a_non_pr_url(self):
        assert pr_number_from_url("https://github.com/o/r/issues/42") is None
        assert pr_number_from_url("") is None
