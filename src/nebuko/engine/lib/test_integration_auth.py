"""RED tests for the App-installation-token auth handling (issue #1220).

Symptom: in GitHub App mode the engine's agent-start comment lands minutes
after the job starts; with a PAT it lands immediately.

Cause under test: the engine resolves its identity with ``GET /user``. A PAT
owner gets ``200``, but an App *installation* token is an integration token:
GitHub answers ``403 Resource not accessible by integration``. The transport's
:class:`GitHubTransport._classify` maps a bare 403 to
``SECONDARY_RATE_LIMIT`` (retryable), so the identity probe burns four
attempts with 60 s secondary cooldowns (~3 min) before failing closed to
``(0, "")`` — and only then does the run proceed to
``notify_agent_started()``.

The contract under test: an integration denial is deterministic *auth*
information, not a rate limit. It must be detected by
``github_client.classify_integration_denial`` and treated as non-retryable,
so the engine fails fast to its best-effort identity fallback.

These tests are expected to FAIL against the skeleton (RED evidence for the
spec step).
"""

from unittest.mock import MagicMock, patch

import pytest

from nebuko.engine.lib.github_client import (
    ApiCallResult,
    ApiErrorKind,
    GitHubTransport,
    classify_integration_denial,
)


def _denial(status=403, detail="gh: Resource not accessible by integration"):
    return ApiCallResult(
        ok=False, status=status, payload=detail, detail=detail, error_kind=None
    )


def _primary_exhausted():
    return ApiCallResult(
        ok=False,
        status=403,
        payload="API rate limit exceeded",
        detail="API rate limit exceeded",
        error_kind=None,
    )


def _transport(**kwargs):
    kwargs.setdefault("max_attempts", 1)
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return GitHubTransport(**kwargs)


class TestClassifyIntegrationDenial:
    """classify_integration_denial detects the App-token 403 signature."""

    def test_given_integration_denial_403_when_classified_then_true(self):
        assert classify_integration_denial(_denial()) is True

    def test_given_successful_call_when_classified_then_false(self):
        ok = ApiCallResult(
            ok=True, status=200, payload="{}", detail="", error_kind=None
        )
        assert classify_integration_denial(ok) is False

    def test_given_primary_rate_limit_403_when_classified_then_false(self):
        # A rate-limit 403 must not be mistaken for an integration denial.
        assert classify_integration_denial(_primary_exhausted()) is False

    def test_given_secondary_rate_limit_403_when_classified_then_false(self):
        secondary = ApiCallResult(
            ok=False,
            status=403,
            payload="You have exceeded a secondary rate limit",
            detail="You have exceeded a secondary rate limit",
            error_kind=None,
        )
        assert classify_integration_denial(secondary) is False


class TestIntegrationDenialIsNotRetried:
    """A ``GET /user`` integration denial fails fast instead of cooling down."""

    @patch("github_client.subprocess.run")
    def test_given_denial_on_user_call_when_called_then_no_sleep(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="gh: Resource not accessible by integration (HTTP 403)",
        )
        sleeps: list[float] = []
        transport = _transport(
            sleep=sleeps.append,
            monotonic=lambda: 0.0,
        )

        transport.call(["api", "/user"], mutative=False)

        assert sleeps == [], (
            "integration denial must fail fast, not sit out a 60 s cooldown"
        )


class TestEngineActorFailsFast:
    """engine_actor() resolves without retry stalls on an App token."""

    @patch("github_client.get_authenticated_user")
    def test_given_unresolvable_user_when_resolved_then_zero_actor_fast(
        self, mock_get_user
    ):
        from nebuko.adapters.github import GitHubAdapter

        mock_get_user.return_value = None
        adapter = GitHubAdapter.__new__(GitHubAdapter)
        adapter._engine_actor = None
        adapter._client_handle = lambda: MagicMock()

        assert adapter.engine_actor() == (0, "")
