"""App-installation detection contract for ``nebuko init`` (issue #1025).

``AppInstallationResolver`` decides the mode: a 200 from the repository
installation endpoint means the Nebuko App is installed and the PAT prompt is
skipped; a 404 means PAT mode. These tests pin both answers and the id.
"""

from __future__ import annotations

import json

from nebuko.engine.init.app_installation import AppInstallationResolver

OWNER = "acme"
REPO = "widgets"
SLUG = "nebuko"


def _result(status: int, payload: str = ""):
    from github_client import ApiCallResult, ApiErrorKind

    return ApiCallResult(
        ok=200 <= status < 300,
        status=status,
        payload=payload,
        error_kind=None if 200 <= status < 300 else ApiErrorKind.NOT_FOUND,
        detail="",
    )


class FakeClient:
    """Answers the installation probe with one canned response."""

    def __init__(self, response):
        self.response = response
        self.calls: list[list[str]] = []

    def call(self, args, **kwargs):
        self.calls.append(list(args))
        return self.response

    def endpoint(self) -> str:
        joined = " ".join(self.calls[0])
        return next(part for part in self.calls[0] if part.startswith("repos/"))


def _resolver(response) -> tuple[AppInstallationResolver, FakeClient]:
    client = FakeClient(response)
    return AppInstallationResolver(client, OWNER, REPO, SLUG), client


def test_given_the_app_is_installed_when_probing_then_it_reports_installed():
    resolver, client = _resolver(_result(200, json.dumps({"id": 4711})))

    assert resolver.is_installed() is True
    assert client.endpoint() == f"repos/{OWNER}/{REPO}/installation"


def test_given_the_app_is_not_installed_when_probing_then_it_reports_pat_mode():
    resolver, _ = _resolver(_result(404, json.dumps({"message": "Not Found"})))

    assert resolver.is_installed() is False


def test_given_the_app_is_installed_when_asking_for_the_id_then_it_returns_it():
    resolver, _ = _resolver(_result(200, json.dumps({"id": 4711})))

    assert resolver.installation_id() == 4711


def test_given_the_app_is_not_installed_when_asking_for_the_id_then_it_is_none():
    resolver, _ = _resolver(_result(404, json.dumps({"message": "Not Found"})))

    assert resolver.installation_id() is None
