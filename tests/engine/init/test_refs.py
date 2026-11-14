"""Release-ref resolution for ``nebuko init`` (issue #991)."""

from __future__ import annotations

import subprocess

from nebuko.engine.init.refs import (
    DEFAULT_WORKFLOW_REF,
    GitTagRefResolver,
    StaticRefResolver,
)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _Runner:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if self.error is not None:
            raise self.error
        return self.result


def test_latest_is_the_semver_max_of_the_remote_tags():
    runner = _Runner(_completed("v0.0.1\nv0.0.10\nv0.0.2\nnightly\n"))
    assert GitTagRefResolver(runner=runner).latest() == "v0.0.10"
    assert runner.calls == [
        ["gh", "api", "repos/nebuko/nebuko/tags", "--paginate", "--jq", ".[].name"]
    ]


def test_latest_keeps_the_tag_spelling_without_the_v_prefix():
    runner = _Runner(_completed("1.4.0\nv1.3.9\n"))
    assert GitTagRefResolver(runner=runner).latest() == "1.4.0"


def test_latest_falls_back_when_gh_fails():
    runner = _Runner(_completed(returncode=1, stderr="gh: not logged in"))
    assert GitTagRefResolver(runner=runner).latest() == DEFAULT_WORKFLOW_REF


def test_latest_falls_back_when_gh_is_missing():
    runner = _Runner(error=FileNotFoundError("gh"))
    assert GitTagRefResolver(runner=runner).latest() == DEFAULT_WORKFLOW_REF


def test_latest_falls_back_when_no_tag_is_semver():
    runner = _Runner(_completed("nightly\nlatest\n"))
    assert GitTagRefResolver(runner=runner).latest() == DEFAULT_WORKFLOW_REF


def test_static_resolver_returns_its_ref():
    assert StaticRefResolver("v9.9.9").latest() == "v9.9.9"


def test_default_runner_shells_out_to_gh(monkeypatch):
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs))
        return _completed("v0.0.3\n")

    monkeypatch.setattr("nebuko.engine.init.refs.subprocess.run", fake_run)
    assert GitTagRefResolver().latest() == "v0.0.3"
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is True
