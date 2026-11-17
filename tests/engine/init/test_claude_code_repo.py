"""``nebuko init claude-code-repo`` (ADR-0017 Lane D): workflow guard and token handling."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from nebuko.engine.init import claude_code_repo as ccr

SHA = "a" * 40
LOGIN = "octocat"


def _workflow() -> dict:
    return yaml.safe_load(ccr.render_workflow(SHA))


def _owner_guard_script() -> str:
    steps = _workflow()["jobs"]["owner-guard"]["steps"]
    return steps[0]["run"]


def _run_guard(actor: str, owner: str) -> int:
    import os

    # Mirror the step's env block: the script reads ACTOR and OWNER only.
    env = {"PATH": os.environ.get("PATH", ""), "ACTOR": actor, "OWNER": owner}
    return subprocess.run(["/bin/sh", "-c", _owner_guard_script()], env=env, capture_output=True).returncode


def test_agent_job_is_gated_behind_the_owner_guard() -> None:
    jobs = _workflow()["jobs"]
    assert jobs["agent"]["needs"] == "owner-guard"


def test_owner_guard_compares_actor_to_repository_owner() -> None:
    step = _workflow()["jobs"]["owner-guard"]["steps"][0]
    assert step["env"]["ACTOR"] == "${{ github.event.client_payload.actor }}"
    assert step["env"]["OWNER"] == "${{ github.repository_owner }}"


@pytest.mark.parametrize(
    ("actor", "owner", "expected"),
    [(LOGIN, LOGIN, 0), ("mallory", LOGIN, 1), ("", LOGIN, 1)],
)
def test_owner_guard_refuses_any_actor_other_than_owner(actor: str, owner: str, expected: int) -> None:
    assert _run_guard(actor, owner) == expected


def test_reusable_workflow_is_pinned_to_the_sha() -> None:
    assert _workflow()["jobs"]["agent"]["uses"] == (
        f"nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@{SHA}"
    )


def test_render_rejects_a_branch_name_pin() -> None:
    with pytest.raises(ValueError):
        ccr.render_workflow("main")


class _Fake:
    """Records every ``gh`` call; answers the lookups the flow needs."""

    def __init__(self, *, repo_exists: bool = False) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.repo_exists = repo_exists

    def __call__(self, args, capture_output=True, text=True, input=None):  # noqa: A002
        self.calls.append((list(args), input))
        out = ""
        rc = 0
        if args[:3] == ["gh", "api", "user"]:
            out = LOGIN
        elif args[:2] == ["gh", "repo"] and args[2] == "view":
            rc = 0 if self.repo_exists else 1
        elif args[:2] == ["gh", "api"] and "commits/" in args[2]:
            out = SHA
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")


def test_token_travels_on_stdin_only_and_never_in_argv() -> None:
    token = "sk-ant-oat01-SECRETVALUE"
    fake = _Fake()
    code, message = ccr.run_claude_code_repo(runner=fake, env={ccr.TOKEN_SECRET: token})
    assert code == 0
    assert token not in message
    for args, _ in fake.calls:
        assert all(token not in part for part in args)
    secret_calls = [(a, i) for a, i in fake.calls if a[1:3] == ["secret", "set"]]
    assert secret_calls and secret_calls[0][1] == token
    assert secret_calls[0][0][-2:] == ["--repo", f"{LOGIN}/{ccr.USER_REPO_NAME}"]


def test_workflow_upload_carries_the_owner_guard() -> None:
    fake = _Fake()
    ccr.run_claude_code_repo(runner=fake, env={ccr.TOKEN_SECRET: "t"})
    put = [i for a, i in fake.calls if "--method" in a and "PUT" in a]
    assert len(put) == 1
    import base64

    body = json.loads(put[0])
    workflow = base64.b64decode(body["content"]).decode()
    assert "github.repository_owner" in workflow
    assert "owner-guard" in workflow


def test_existing_repo_is_refused_before_any_write() -> None:
    fake = _Fake(repo_exists=True)
    code, message = ccr.run_claude_code_repo(runner=fake, env={ccr.TOKEN_SECRET: "t"})
    assert code == 1
    assert "already exists" in message
    assert not any(a[1:2] == ["repo"] and a[2] == "create" for a, _ in fake.calls)
    assert not any(a[1:3] == ["secret", "set"] for a, _ in fake.calls)


def test_missing_token_is_a_usage_error_and_creates_nothing() -> None:
    fake = _Fake()
    code, _ = ccr.run_claude_code_repo(
        runner=fake, env={}, secret_fn=lambda _prompt: ""
    )
    assert code == 2
    assert not any(a[1:2] == ["repo"] and a[2] == "create" for a, _ in fake.calls)
