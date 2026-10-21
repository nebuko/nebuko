"""Structural guard for the workflow-pinned workspace checkout (issue #862).

A workflow pinned via ``uses: <repo>/…@<sha1|tag>`` used to run against a
``main`` checkout: the ``run-task`` ``Checkout repository`` step was bare, so
``actions/checkout`` fell back to the event default. These tests pin the YAML
contract that fixes it — the ``checkout_ref`` input, the guarded
``job.workflow_sha`` expression on ``run-task``, the untouched
``agent-metrics`` self-checkout, and the ``NEBUKO_WORKFLOW_REF`` env wiring.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))


def _steps(job: str) -> list[dict]:
    return _workflow()["jobs"][job]["steps"]


def _checkout_step(job: str, name: str) -> dict:
    return next(s for s in _steps(job) if s.get("name") == name)


class TestCheckoutRefInput:
    def test_declared_with_empty_default(self):
        inputs = _workflow()[True]["workflow_call"]["inputs"]
        assert "checkout_ref" in inputs
        assert inputs["checkout_ref"]["default"] == ""
        assert inputs["checkout_ref"]["type"] == "string"


class TestRunTaskCheckoutPin:
    def _ref(self) -> str:
        return _checkout_step("run-task", "Checkout repository")["with"]["ref"]

    def test_explicit_override_wins(self):
        assert "inputs.checkout_ref" in self._ref()

    def test_auto_pins_to_the_workflow_commit(self):
        assert "job.workflow_sha" in self._ref()

    def test_guarded_by_the_same_repo_comparison(self):
        # Without the guard a cross-repo caller would checkout a SHA that does
        # not exist in their repo. The comparison is load-bearing.
        assert "job.workflow_repository == github.repository" in self._ref()

    def test_credentials_not_persisted(self):
        step = _checkout_step("run-task", "Checkout repository")
        assert step["with"]["persist-credentials"] is False


class TestAgentMetricsSelfCheckoutUnchanged:
    def _step(self) -> dict:
        return _checkout_step("agent-metrics", "Checkout nebuko repo (reusable workflow mode)")

    def test_still_pins_to_the_workflow_repo_and_sha(self):
        step = self._step()
        assert step["with"]["repository"] == "${{ job.workflow_repository }}"
        assert step["with"]["ref"] == "${{ job.workflow_sha }}"


class TestWorkflowRefEnv:
    def test_engine_step_forwards_job_workflow_ref(self):
        engine = next(
            s for s in _steps("run-task")
            if s.get("name") == "Run orchestration engine"
        )
        assert engine["env"]["NEBUKO_WORKFLOW_REF"] == "${{ job.workflow_ref }}"


class TestOidcBrokerToken:
    """The workflow resolves its GitHub token from the OIDC broker (issue #1023).

    ``GH_PAT`` is now only a fallback: the broker token has to reach every
    consumer (engine, ``git push`` extraheader, metrics job).
    """

    MINT_STEP = "Acquire GitHub token (OIDC broker)"

    def test_workflow_requests_an_oidc_token(self):
        assert _workflow()["permissions"]["id-token"] == "write"

    def test_gh_pat_is_an_optional_secret(self):
        secret = _workflow()[True]["workflow_call"]["secrets"]["GH_PAT"]
        assert secret["required"] is False

    def test_each_job_mints_before_it_needs_the_token(self):
        # $GITHUB_ENV does not cross jobs, so both jobs mint their own token.
        for job in ("run-task", "agent-metrics"):
            names = [s.get("name") for s in _steps(job)]
            assert self.MINT_STEP in names
            assert names.index(self.MINT_STEP) < names.index("Checkout repository")

    def test_broker_url_defaults_to_a_repo_variable(self):
        mint = _checkout_step("run-task", self.MINT_STEP)
        assert "vars.NEBUKO_BROKER_URL" in mint["env"]["BROKER_URL"]
        assert "|| 'https://" in mint["env"]["BROKER_URL"]

    def test_mint_step_never_fails_and_exports_gh_token(self):
        mint = _checkout_step("run-task", self.MINT_STEP)
        assert "continue-on-error" not in mint
        assert "GH_TOKEN=$TOKEN" in mint["run"]
        assert "GITHUB_ENV" in mint["run"]
        assert "::add-mask::" in mint["run"]

    def test_fallback_chain_prefers_pat_over_github_token(self):
        run = _checkout_step("run-task", self.MINT_STEP)["run"]
        assert run.index("FALLBACK_PAT") < run.index("FALLBACK_GITHUB_TOKEN")

    def test_cross_repo_checkouts_of_public_engine_carry_no_token(self):
        for job, name in (
            ("run-task", "Checkout nebuko engine (cross-repo caller)"),
            ("agent-metrics", "Checkout nebuko repo (reusable workflow mode)"),
        ):
            assert "token" not in _checkout_step(job, name)["with"]

    def test_engine_and_metrics_use_the_resolved_token(self):
        engine = next(s for s in _steps("run-task") if s.get("name") == "Run orchestration engine")
        assert engine["env"]["GH_TOKEN"] == "${{ env.GH_TOKEN }}"
        metrics = next(s for s in _steps("agent-metrics") if s.get("name") == "Aggregate agent metrics")
        assert metrics["env"]["GITHUB_TOKEN"] == "${{ env.GH_TOKEN }}"

    def test_git_push_extraheader_uses_the_resolved_token(self):
        step = next(s for s in _steps("run-task") if s.get("name") == "Install OpenHands dependencies")
        assert "secrets." not in step.get("env", {}).get("GH_TOKEN", "")
        assert "${GH_TOKEN}" in step["run"]
