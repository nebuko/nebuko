"""Core plan/execute contract for ``nebuko init`` (issue #1272, rev 9)."""

from __future__ import annotations

import pytest

from nebuko.engine.init.core import (
    AGENT_CONFIG_PATH,
    WORKFLOW_PATH,
    GitHubInitClient,
    InitRequest,
    RenderInputs,
    SecretAccess,
    SecretTarget,
    SealedSecret,
    execute_init,
    plan_init,
)
from nebuko.engine.init.manifest import Manifest, ProviderSpec

PROVIDER_SECRETS = ("OPENROUTER_API_KEY",)


def _manifest() -> Manifest:
    return Manifest(
        schema_version=1,
        workflow_filename="nebuko.yaml",
        caller_workflow_template="nebuko-caller.yaml",
        providers={
            "openrouter": ProviderSpec(
                name="openrouter",
                description="OpenRouter (default)",
                secret_names=PROVIDER_SECRETS,
                template_path="defaults/agents/agent-config-openrouter.yaml",
            )
        },
    )


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
        manifest=_manifest(),
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
        manifest=_manifest(),
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
            manifest=_manifest(),
            caller_template="workflow-body",
            agent_config="agent-config-body",
            existing_files={},
            existing_secrets=frozenset(),
        )


class FakeClient(GitHubInitClient):
    def __init__(self):
        self.put_calls: list[tuple] = []
        self.access_calls: list[tuple[str, ...]] = []
        self.committed: dict[str, str] | None = None

    def get_existing_files(self, target, paths):
        return {}

    def existing_secrets(self, target):
        return frozenset()

    def upsert_branch_and_commit(self, target, branch, files):
        self.committed = files

    def get_or_open_pull_request(self, target, branch, title, body):
        return "https://github.com/acme/widgets/pull/7"

    def put_secret(self, target, name, value, visibility=None, repos=None):
        self.put_calls.append((name, value, visibility, repos))

    def existing_secret_access(self, target, names):
        return {}

    def add_repo_access(self, target, name):
        self.access_calls.append((name,))


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
        manifest=_manifest(),
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files={},
        existing_secrets=frozenset(),
    )
    execute_init(plan, client, secrets={"OPENROUTER_API_KEY": "sk-or-plain", "GH_PAT": "ghp_x"})
    assert ("OPENROUTER_API_KEY", "sk-or-plain") in client.put_calls
    assert ("GH_PAT", "ghp_x") in client.put_calls


# Given a plan that renders both files, when planned, then the rendered map
# carries the agent config verbatim at AGENT_CONFIG_PATH and the workflow at
# its documented path (N36 — corrected from the provider-named path)
def test_plan_init_renders_workflow_and_agent_config():
    plan = _plan()
    assert set(plan.rendered) == {
        AGENT_CONFIG_PATH,
        WORKFLOW_PATH,
    }
    assert plan.rendered[AGENT_CONFIG_PATH] == "agent-config-body"
    assert plan.rendered[WORKFLOW_PATH] == "workflow-body"


# Given a plan that renders both files, when planned, then the workflow carries
# the pin, the config path and the runner label from the render inputs
def test_plan_init_renders_the_workflow_placeholders():
    plan = plan_init(
        request=_request(),
        manifest=_manifest(),
        caller_template=(
            "pin=${pin_ref} path=${agent_config_path} runs=${runs_on}\n${secrets_block}"
        ),
        agent_config="agent-config-body",
        existing_files={},
        existing_secrets=frozenset(),
    )
    workflow = plan.rendered[WORKFLOW_PATH]
    assert "pin=v1.2.3" in workflow
    assert f"path={AGENT_CONFIG_PATH}" in workflow
    assert 'runs_on="ubuntu-latest"' not in workflow
    assert "runs=ubuntu-latest" in workflow
    assert "      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}" in workflow


# Given a secret whose put_secret fails, when executed, then the secret is
# recorded as failed with the error detail and success is False

class FailingClient(FakeClient):
    def put_secret(self, target, name, value):
        raise RuntimeError("gh secret set failed: boom")


def test_execute_init_records_failed_secret():
    plan = _plan()
    outcome = execute_init(plan, FailingClient(), secrets={"OPENROUTER_API_KEY": "sk-or"})
    assert not outcome.success
    assert outcome.secrets[0].name == "OPENROUTER_API_KEY"
    assert outcome.secrets[0].status == "failed"
    assert "boom" in outcome.secrets[0].detail


# Given a secret with no value provided, when executed, then it is recorded as
# failed with a clear detail (never a KeyError, never the bare name as detail)
def test_execute_init_records_missing_secret_value_as_failed():
    plan = _plan()
    outcome = execute_init(plan, FakeClient(), secrets={})
    assert not outcome.success
    assert outcome.secrets[0].status == "failed"
    assert "no value provided" in outcome.secrets[0].detail


# ── rev 13 (N30–N35): access-preserving org secrets ─────────────────────────


def _org_request(force: bool = False) -> InitRequest:
    return InitRequest(
        target=SecretTarget(scope="org", repo="acme/widgets", org="acme", selected_repos=("widgets",)),
        provider="openrouter",
        render=RenderInputs(pin_ref="v1.2.3"),
        pat_mode=False,
        force=force,
    )


def _org_plan(
    existing_secrets=frozenset(),
    access=None,
    force=False,
    request=None,
):
    return plan_init(
        request=request or _org_request(force=force),
        manifest=_manifest(),
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files={},
        existing_secrets=existing_secrets,
        secret_access=access,
    )


# Given an org secret the target repo can read, when planned without --force,
# then the action is skip_existing (no value needed, no prompt) (N32 row 1)
def test_plan_init_org_readable_secret_skips_existing_without_force():
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=True, visibility="all")},
    )
    assert plan.secret_actions == {"OPENROUTER_API_KEY": "skip_existing"}


# Given an org secret the target repo can read, when planned with --force,
# then the action is update (prompt for the value; keep existing visibility) (N32 row 1)
def test_plan_init_org_readable_secret_updates_with_force():
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=True, visibility="selected", repo_listed=True)},
        force=True,
    )
    assert plan.secret_actions == {"OPENROUTER_API_KEY": "update"}


# Given a selected org secret that does not list the target repo, when planned
# without --force, then the action is add_repo_access (no value, no prompt) (N32 row 2)
def test_plan_init_org_selected_secret_not_listed_adds_repo_access():
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=False, visibility="selected", repo_listed=False)},
    )
    assert plan.secret_actions == {"OPENROUTER_API_KEY": "add_repo_access"}


# Given a selected org secret that does not list the target repo, when planned
# with --force, then the action is update + add the target repo to the list (N32 row 2)
def test_plan_init_org_selected_secret_not_listed_with_force_updates_and_adds_access():
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=False, visibility="selected", repo_listed=False)},
        force=True,
    )
    assert plan.secret_actions == {"OPENROUTER_API_KEY": "update"}


# Given a private org secret and a public target repo, when planned without
# --force, then the action is report_inaccessible — never widen access (N32 row 3)
def test_plan_init_org_private_secret_public_repo_reports_inaccessible():
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=False, visibility="private", repo_listed=None)},
    )
    assert plan.secret_actions == {"OPENROUTER_API_KEY": "report_inaccessible"}


# Given a private org secret and a public target repo, when planned with --force,
# then the action stays report_inaccessible (N32 row 3, force column)
def test_plan_init_org_private_secret_public_repo_stays_inaccessible_with_force():
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=False, visibility="private", repo_listed=None)},
        force=True,
    )
    assert plan.secret_actions == {"OPENROUTER_API_KEY": "report_inaccessible"}


# Given an org secret name absent from the org, when planned, then the action is
# create with the selected + (target repo,) default (N8 stands)
def test_plan_init_org_new_secret_creates_with_selected_visibility():
    plan = _org_plan(existing_secrets=frozenset(), access={})
    assert plan.secret_actions == {"OPENROUTER_API_KEY": "create"}


# Given an org target whose org is not the repo owner, when planned, then
# ValueError before any I/O (N34)
def test_plan_init_org_not_repo_owner_raises_value_error():
    with pytest.raises(ValueError, match="not the owner"):
        _org_plan(
            request=InitRequest(
                target=SecretTarget(scope="org", repo="acme/widgets", org="other", selected_repos=("widgets",)),
                provider="openrouter",
                render=RenderInputs(pin_ref="v1.2.3"),
                pat_mode=False,
                force=False,
            )
        )


# Given visibility="selected" with no selected repos, when the SecretTarget is
# constructed, then ValueError (N35 — mirrors gh's rejection)
def test_secret_target_selected_with_no_repos_raises_value_error():
    with pytest.raises(ValueError):
        SecretTarget(scope="org", repo="acme/widgets", org="acme", visibility="selected", selected_repos=())


# Given an add_repo_access action, when executed, then client.add_repo_access is
# called and no value is sent and the repo list is never replaced (N33)
def test_execute_init_add_repo_access_never_sends_value_or_replaces_list():
    client = FakeClient()
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=False, visibility="selected", repo_listed=False)},
    )
    outcome = execute_init(plan, client, secrets={})
    assert client.access_calls == [("OPENROUTER_API_KEY",)]
    assert client.put_calls == []
    assert outcome.secrets[0].status == "add_repo_access"
    assert outcome.success


# Given a report_inaccessible action, when executed, then the status is recorded
# with a clear detail and success is False — never raised (N33)
def test_execute_init_records_report_inaccessible_and_fails():
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=False, visibility="private", repo_listed=None)},
    )
    outcome = execute_init(plan, FakeClient(), secrets={})
    status = outcome.secrets[0]
    assert status.status == "report_inaccessible"
    assert "private" in status.detail and "cannot read" in status.detail
    assert not outcome.success


# Given an update action on an org secret, when executed, then put_secret receives
# the EXISTING visibility and full existing repo list (never gh's defaults) (N33)
def test_execute_init_org_update_keeps_existing_visibility_and_repo_list():
    client = FakeClient()
    plan = _org_plan(
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
        access={"OPENROUTER_API_KEY": SecretAccess(visible=True, visibility="selected", repo_listed=True)},
        force=True,
    )
    execute_init(plan, client, secrets={"OPENROUTER_API_KEY": "new-value"})
    assert client.put_calls == [("OPENROUTER_API_KEY", "new-value", "selected", ("widgets",))]
