"""Core plan/execute contract for ``nebuko init`` (issue #1272, rev 9)."""

from __future__ import annotations

import pytest

from nebuko.engine.init.core import (
    GitHubInitClient,
    InitRequest,
    RenderInputs,
    SecretTarget,
    SealedSecret,
    execute_init,
    plan_init,
)

PROVIDER_SECRETS = ("OPENROUTER_API_KEY",)


def _request(pat_mode: bool = False, force: bool = False, scope: str = "repo") -> InitRequest:
    org = "acme" if scope == "org" else None
    return InitRequest(
        target=SecretTarget(scope=scope, repo="acme/widgets", org=org),
        provider="openrouter",
        render=RenderInputs(pin_ref="v1.2.3"),
        pat_mode=pat_mode,
        force=force,
    )


def _plan(existing_secrets=frozenset(), existing_files=None, request=None, force=False):
    return plan_init(
        request=request or _request(force=force),
        manifest=None,
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files=existing_files or {},
        existing_secrets=existing_secrets,
    )


# Given a fresh repo with no existing secrets and pat_mode=False, when planned,
# then exactly the provider secrets are "create" and GH_PAT is absent
def test_plan_init_fresh_repo_all_create():
    plan = _plan(existing_secrets=frozenset())
    assert plan.secret_actions == {name: "create" for name in PROVIDER_SECRETS}


# Given the same inputs with pat_mode=True, when planned, then GH_PAT is included
def test_plan_init_includes_gh_pat_only_in_pat_mode():
    plan = plan_init(
        request=_request(pat_mode=True),
        manifest=None,
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files={},
        existing_secrets=frozenset(),
    )
    assert plan.secret_actions == {**{name: "create" for name in PROVIDER_SECRETS}, "GH_PAT": "create"}


# Given an existing secret, when planned, then its action is skip_existing;
# with force=True the existing secret is planned as "update"
def test_plan_init_skips_existing_secret():
    plan = _plan(existing_secrets=frozenset({"OPENROUTER_API_KEY"}))
    assert plan.secret_actions == {"OPENROUTER_API_KEY": "skip_existing"}

    forced = _plan(existing_secrets=frozenset({"OPENROUTER_API_KEY"}), force=True)
    assert forced.secret_actions == {"OPENROUTER_API_KEY": "update"}


# Given an existing target file and force=False, when planned, then a conflict is recorded
def test_plan_init_records_conflicts_without_force():
    plan = _plan(existing_files={".github/workflows/nebuko.yaml": True})
    assert ".github/workflows/nebuko.yaml" in plan.conflicts


# Given an org-scope target without an org name, when planned, then ValueError
def test_plan_init_rejects_org_scope_without_org():
    request = InitRequest(
        target=SecretTarget(scope="org", repo="acme/widgets", org=None),
        provider="openrouter",
        render=RenderInputs(pin_ref="v1.2.3"),
        pat_mode=False,
        force=False,
    )
    with pytest.raises(ValueError):
        plan_init(
            request=request,
            manifest=None,
            caller_template="workflow-body",
            agent_config="agent-config-body",
            existing_files={},
            existing_secrets=frozenset(),
        )


class FakeClient(GitHubInitClient):
    def __init__(self):
        self.put_calls: list[tuple[str, object]] = []
        self.committed: dict[str, str] | None = None

    def get_existing_files(self, target, paths):
        return {}

    def existing_secrets(self, target):
        return frozenset()

    def upsert_branch_and_commit(self, target, branch, files):
        self.committed = files

    def get_or_open_pull_request(self, target, branch, title, body):
        return "https://github.com/acme/widgets/pull/7"

    def put_secret(self, target, name, value):
        self.put_calls.append((name, value))


# Given a plan, when executed, then the branch is upserted, the PR reused-or-opened,
# and each secret in secret_actions gets a status with its value passed through verbatim
def test_execute_init_reports_per_secret_status():
    client = FakeClient()
    plan = _plan()
    sealed = SealedSecret(encrypted_value="enc", key_id="k1")
    outcome = execute_init(plan, client, secrets={"OPENROUTER_API_KEY": sealed})
    assert outcome.pr_url == "https://github.com/acme/widgets/pull/7"
    assert outcome.success
    assert [s.name for s in outcome.secrets] == ["OPENROUTER_API_KEY"]
    assert client.put_calls == [("OPENROUTER_API_KEY", sealed)]


# Given a PAT-mode plan, when executed, then put_secret receives the plaintext str
def test_execute_init_passes_plaintext_in_pat_mode():
    client = FakeClient()
    plan = plan_init(
        request=_request(pat_mode=True),
        manifest=None,
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files={},
        existing_secrets=frozenset(),
    )
    execute_init(plan, client, secrets={"OPENROUTER_API_KEY": "sk-or-plain", "GH_PAT": "ghp_x"})
    assert ("OPENROUTER_API_KEY", "sk-or-plain") in client.put_calls
    assert ("GH_PAT", "ghp_x") in client.put_calls
