"""Repository-variable contract for ``nebuko init`` (issue #1025).

``RepoVariableWriter`` upserts ``NEBUKO_BROKER_URL`` through the repo-variables
API: create when absent, update in place when present, never touch a variable
whose name differs.
"""

from __future__ import annotations

import json

from nebuko.engine.init.repo_variables import RepoVariableWriter

OWNER = "acme"
REPO = "widgets"
BASE = f"repos/{OWNER}/{REPO}/actions/variables"


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
    """Records every call and answers by endpoint shape."""

    def __init__(self, existing: dict[str, str] | None = None):
        self.existing = dict(existing or {})
        self.calls: list[tuple[list[str], dict]] = []

    def call(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        joined = " ".join(args)
        if f"{BASE}/NEBUKO_BROKER_URL" in joined and "--method" not in args:
            if "NEBUKO_BROKER_URL" not in self.existing:
                return _result(404, json.dumps({"message": "Not Found"}))
            return _result(200, json.dumps({"name": "NEBUKO_BROKER_URL", "value": self.existing["NEBUKO_BROKER_URL"]}))
        if "--method" in args and "PATCH" in args:
            payload = json.loads(kwargs.get("input_data") or "{}")
            self.existing[payload["name"]] = payload["value"]
            return _result(204)
        if "--method" in args and "POST" in args:
            payload = json.loads(kwargs.get("input_data") or "{}")
            self.existing[payload["name"]] = payload["value"]
            return _result(201, json.dumps({"name": payload["name"]}))
        return _result(404, json.dumps({"message": "Not Found"}))

    def mutative_calls(self):
        return [args for args, _ in self.calls if "--method" in args]


def test_given_the_variable_does_not_exist_when_getting_then_it_is_none():
    writer = RepoVariableWriter(FakeClient(), OWNER, REPO)

    assert writer.get("NEBUKO_BROKER_URL") is None


def test_given_the_variable_exists_when_getting_then_it_returns_the_value():
    client = FakeClient({"NEBUKO_BROKER_URL": "https://broker.example"})
    writer = RepoVariableWriter(client, OWNER, REPO)

    assert writer.get("NEBUKO_BROKER_URL") == "https://broker.example"


def test_given_no_variable_when_setting_then_it_creates_it():
    client = FakeClient()
    writer = RepoVariableWriter(client, OWNER, REPO)

    writer.set("NEBUKO_BROKER_URL", "https://broker.example")

    assert client.existing == {"NEBUKO_BROKER_URL": "https://broker.example"}
    assert any("POST" in args for args in client.mutative_calls())


def test_given_the_variable_exists_when_setting_then_it_updates_in_place():
    client = FakeClient({"NEBUKO_BROKER_URL": "https://old.example"})
    writer = RepoVariableWriter(client, OWNER, REPO)

    writer.set("NEBUKO_BROKER_URL", "https://new.example")

    assert client.existing == {"NEBUKO_BROKER_URL": "https://new.example"}
    assert any("PATCH" in args for args in client.mutative_calls())
