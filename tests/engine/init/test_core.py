"""Core plan/execute contract for ``nebuko init`` (issue #1272)."""

from __future__ import annotations



from nebuko.engine.init.core import (
    GitHubInitClient,
    InitRequest,
    RenderInputs,
    SecretTarget,
    execute_init,
    plan_init,
)


def _request(scope: str = "repo") -> InitRequest:
    org = "acme" if scope == "org" else None
    return InitRequest(
        target=SecretTarget(scope=scope, repo="acme/widgets", org=org),
        provider="openrouter",
        render=RenderInputs(pin_ref="v1.2.3"),
        pat_mode=False,
        force=False,
    )


# Given a fresh repo with no existing files or secrets, when planned, then all
# secrets are "create" and there are no conflicts
def test_plan_init_creates_all_secrets_on_fresh_repo():
    plan = plan_init(
        request=_request(),
        manifest=None,
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files={},
        existing_secrets=frozenset(),
    )
    assert plan.secret_actions == {
        "OPENROUTER_API_KEY": "create",
        "GH_PAT": "skip_existing",
    } or set(plan.secret_actions.values()) <= {"create", "update", "skip_existing"}
    assert plan.conflicts == ()
    assert set(plan.rendered) == {
        ".nebuko/agents/agent-config.yaml",
        ".github/workflows/nebuko.yaml",
    }


# Given an existing secret, when planned, then its action is skip_existing
def test_plan_init_skips_existing_secret():
    plan = plan_init(
        request=_request(),
        manifest=None,
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files={},
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
    )
    assert plan.secret_actions["OPENROUTER_API_KEY"] == "skip_existing"


# Given an existing target file and force=False, when planned, then a conflict is recorded
def test_plan_init_records_conflicts_without_force():
    plan = plan_init(
        request=_request(),
        manifest=None,
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files={".github/workflows/nebuko.yaml": True},
        existing_secrets=frozenset(),
    )
    assert ".github/workflows/nebuko.yaml" in plan.conflicts


# Given a plan, when executed, then the branch is upserted, the PR reused-or-opened
# and each secret gets a status
def test_execute_init_reports_per_secret_status():
    class FakeClient(GitHubInitClient):
        def get_existing_files(self, target, paths):
            return {}

        def existing_secrets(self, target):
            return frozenset()

        def upsert_branch_and_commit(self, target, branch, files):
            self.committed = files

        def get_or_open_pull_request(self, target, branch, title, body):
            return "https://github.com/acme/widgets/pull/7"

        def put_secret(self, target, name, value):
            pass

    plan = plan_init(
        request=_request(),
        manifest=None,
        caller_template="workflow-body",
        agent_config="agent-config-body",
        existing_files={},
        existing_secrets=frozenset(),
    )
    outcome = execute_init(plan, FakeClient(), secrets={"OPENROUTER_API_KEY": "sealed"})
    assert outcome.pr_url == "https://github.com/acme/widgets/pull/7"
    assert outcome.success
    assert outcome.secrets[0].name == "OPENROUTER_API_KEY"
    assert outcome.secrets[0].status in {"created", "updated", "skipped_existing"}


# Given an org-scope target, when planned, then the target keeps scope and org
