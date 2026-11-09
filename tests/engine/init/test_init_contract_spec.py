"""Spec tests for the rev 18 web-onboarding contract (issue #1272).

Given/when/then names. These lock the corrected contract: the D1
single-home agent config destination, the D2 `run_init` keyword defaults,
the I/O-only `GitHubInitClient`, module-level `plan_init`/`execute_init`,
and the restored `InitOutcome` / `SealedSecret` / `InitPlan.conflicts`
types.
"""

from __future__ import annotations

import json

import pytest

from nebuko.engine.init import manifest as manifest_module
from nebuko.engine.init.core import (
    AGENT_CONFIG_PATH,
    GitHubInitClient,
    InitOutcome,
    InitPlan,
    InitRequest,
    RenderInputs,
    SealedSecret,
    SecretTarget,
    execute_init,
    plan_init,
)
from nebuko.engine.init.providers import PROVIDER_PROFILES
from nebuko.engine.init.setup import run_init


def _request(**overrides) -> InitRequest:
    defaults = dict(
        target=SecretTarget(scope="repo", repo="acme/widget"),
        provider="openrouter",
        render=RenderInputs(pin_ref="v1.2.3"),
        pat_mode=False,
        force=False,
    )
    defaults.update(overrides)
    return InitRequest(**defaults)


MANIFEST_RAW = json.dumps(
    {
        "schema_version": 1,
        "workflow_filename": "nebuko.yaml",
        "caller_workflow_template": "nebuko-caller.yaml",
        "providers": {
            p.name: {
                "description": p.description,
                "template_path": p.template_path,
                "secret_names": list(p.secret_names),
            }
            for p in PROVIDER_PROFILES
        },
    }
)


class FakeClient(GitHubInitClient):
    def __init__(self, existing_secrets: frozenset[str] = frozenset()) -> None:
        self.existing = existing_secrets
        self.committed: dict[str, str] | None = None
        self.put_calls: list[tuple[str, object]] = []

    def get_existing_files(self, target, paths):
        return {path: False for path in paths}

    def existing_secrets(self, target):
        return self.existing

    def upsert_branch_and_commit(self, target, branch, files):
        self.committed = files

    def get_or_open_pull_request(self, target, branch, title, body):
        return "https://github.com/acme/widget/pull/1"

    def put_secret(self, target, name, value):
        self.put_calls.append((name, value))


def test_given_a_fresh_repo_when_plan_init_runs_then_every_secret_is_created():
    plan = plan_init(
        _request(),
        manifest_module.parse_manifest(MANIFEST_RAW),
        caller_template="workflow: call nebuko",
        agent_config="model: openrouter",
        existing_files={},
        existing_secrets=frozenset(),
    )
    assert set(plan.secret_actions.values()) == {"create"}
    assert plan.conflicts == ()


def test_given_an_existing_secret_when_planning_without_force_then_action_is_skip_existing():
    plan = plan_init(
        _request(),
        manifest_module.parse_manifest(MANIFEST_RAW),
        caller_template="",
        agent_config="",
        existing_files={},
        existing_secrets=frozenset({"OPENROUTER_API_KEY"}),
    )
    assert plan.secret_actions["OPENROUTER_API_KEY"] == "skip_existing"


def test_given_existing_files_when_planning_without_force_then_conflicts_are_recorded():
    plan = plan_init(
        _request(),
        manifest_module.parse_manifest(MANIFEST_RAW),
        caller_template="c",
        agent_config="a",
        existing_files={AGENT_CONFIG_PATH: True, ".github/workflows/nebuko.yaml": False},
        existing_secrets=frozenset(),
    )
    assert plan.conflicts == (AGENT_CONFIG_PATH,)


def test_given_planning_when_rendering_then_agent_config_lands_at_the_single_home_path():
    plan = plan_init(
        _request(),
        manifest_module.parse_manifest(MANIFEST_RAW),
        caller_template="c",
        agent_config="a",
        existing_files={},
        existing_secrets=frozenset(),
    )
    assert set(plan.rendered) == {AGENT_CONFIG_PATH, ".github/workflows/nebuko.yaml"}
    assert AGENT_CONFIG_PATH == ".nebuko/agents/agent-config.yaml"


def test_given_pat_mode_when_planning_then_gh_pat_is_included_only_in_pat_mode():
    pat_plan = plan_init(
        _request(pat_mode=True),
        manifest_module.parse_manifest(MANIFEST_RAW),
        caller_template="",
        agent_config="",
        existing_files={},
        existing_secrets=frozenset(),
    )
    web_plan = plan_init(
        _request(pat_mode=False),
        manifest_module.parse_manifest(MANIFEST_RAW),
        caller_template="",
        agent_config="",
        existing_files={},
        existing_secrets=frozenset(),
    )
    assert "GH_PAT" in pat_plan.secret_actions
    assert "GH_PAT" not in web_plan.secret_actions


def test_given_org_scope_without_org_when_planning_then_value_error_is_raised():
    with pytest.raises(ValueError):
        plan_init(
            _request(target=SecretTarget(scope="org", repo="acme/widget")),
            manifest_module.parse_manifest(MANIFEST_RAW),
            caller_template="",
            agent_config="",
            existing_files={},
            existing_secrets=frozenset(),
        )


def test_given_a_plan_when_execute_init_runs_then_outcome_reports_pr_and_secrets():
    client = FakeClient()
    outcome = execute_init(
        InitPlan(
            request=_request(),
            rendered={AGENT_CONFIG_PATH: "a", ".github/workflows/nebuko.yaml": "w"},
            secret_actions={"OPENROUTER_API_KEY": "create"},
            conflicts=(),
        ),
        client,
        {"OPENROUTER_API_KEY": "sk-value"},
    )
    assert isinstance(outcome, InitOutcome)
    assert outcome.success is True
    assert outcome.pr_url == "https://github.com/acme/widget/pull/1"
    assert client.committed == {AGENT_CONFIG_PATH: "a", ".github/workflows/nebuko.yaml": "w"}


def test_given_web_mode_when_executing_then_sealed_secrets_are_passed_through():
    client = FakeClient()
    sealed = SealedSecret(encrypted_value="enc", key_id="k1")
    execute_init(
        InitPlan(
            request=_request(),
            rendered={},
            secret_actions={"OPENROUTER_API_KEY": "create"},
            conflicts=(),
        ),
        client,
        {"OPENROUTER_API_KEY": sealed},
    )
    assert client.put_calls == [("OPENROUTER_API_KEY", sealed)]


def test_given_a_failing_secret_write_when_executing_then_detail_carries_the_exception_type():
    class FailingClient(FakeClient):
        def put_secret(self, target, name, value):
            raise RuntimeError("bad credentials")

    outcome = execute_init(
        InitPlan(
            request=_request(),
            rendered={},
            secret_actions={"OPENROUTER_API_KEY": "create"},
            conflicts=(),
        ),
        FailingClient(),
        {"OPENROUTER_API_KEY": "v"},
    )
    assert outcome.secrets[0].status == "failed"
    assert "RuntimeError" in outcome.secrets[0].detail


def test_given_run_init_defaults_when_called_without_optional_kwargs_then_no_type_error():
    # D2: pat_mode/non_interactive are keyword defaults so the decision-9
    # propagation path is wireable without passing them.
    import inspect

    signature = inspect.signature(run_init)
    assert signature.parameters["pat_mode"].default is False
    assert signature.parameters["non_interactive"].default is False
