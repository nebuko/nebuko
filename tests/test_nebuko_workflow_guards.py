"""Runner-strategy and credential guards in the reusable nebuko workflow.

The guard step is plain bash driven by env vars, so each case runs the
exact ``run:`` script from the workflow file with a minimal env.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/nebuko-workflow.yaml"
GUARD_STEP = "Validate runner strategy and Claude Code credentials"

BASE_ENV = {
    "EVENT_NAME": "issue_comment",
    "RUNNER_STRATEGY": "static",
    "RUNS_ON": "ubuntu-latest",
    "ACTOR_LOGIN": "octocat",
    "ACTOR_TYPE": "User",
    "CLAUDE_CODE_OAUTH_TOKEN_SECRET": "",
}


def _guard_script() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = doc["jobs"]["run-task"]["steps"]
    (step,) = [s for s in steps if s.get("name") == GUARD_STEP]
    return step["run"]


def _run(tmp_path, **overrides):
    script = tmp_path / "guard.sh"
    script.write_text(_guard_script())
    env = {"PATH": "/usr/bin:/bin"}
    env.update(BASE_ENV)
    env.update(overrides)
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"CLAUDE_CODE_OAUTH_TOKEN_SECRET": "tok"},
        {"RUNNER_STRATEGY": "per-user"},
        {"RUNNER_STRATEGY": "per-user", "RUNS_ON": "self-hosted"},
    ],
    ids=["static-plain", "static-human-with-token", "per-user-clean", "per-user-plain-label"],
)
def test_allowed_combinations_pass(tmp_path, overrides):
    result = _run(tmp_path, **overrides)
    assert result.returncode == 0, result.stdout + result.stderr


def test_guard1_per_user_rejects_shared_claude_token(tmp_path):
    result = _run(tmp_path, RUNNER_STRATEGY="per-user", CLAUDE_CODE_OAUTH_TOKEN_SECRET="tok")
    assert result.returncode != 0
    assert "rejects the shared CLAUDE_CODE_OAUTH_TOKEN" in result.stdout


def test_guard2_per_user_rejects_static_nebuko_label(tmp_path):
    result = _run(tmp_path, RUNNER_STRATEGY="per-user", RUNS_ON="nebuko-alice")
    assert result.returncode != 0
    assert "starting with nebuko-" in result.stdout


@pytest.mark.parametrize(
    "overrides",
    [
        {"ACTOR_TYPE": "Bot", "ACTOR_LOGIN": "dependabot[bot]"},
        {"ACTOR_TYPE": "", "ACTOR_LOGIN": "renovate[bot]"},
        {"EVENT_NAME": "schedule", "ACTOR_LOGIN": ""},
        {"ACTOR_LOGIN": ""},
    ],
    ids=["bot-type", "bot-login-suffix", "schedule", "actor-less"],
)
def test_guard3_refuses_claude_backend_for_non_human_actors(tmp_path, overrides):
    result = _run(tmp_path, CLAUDE_CODE_OAUTH_TOKEN_SECRET="tok", **overrides)
    assert result.returncode != 0
    assert "API-key lane" in result.stdout


TRUSTED = '["nebuko[bot]"]'
CONTINUE_HEADER = "<!-- bot-workflow-terminal --><!-- workflow-continue -->\nnext step"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ACTOR_TYPE": "Bot", "ACTOR_LOGIN": "nebuko[bot]", "TRIGGER_COMMENT_HEADER": CONTINUE_HEADER},
        {"ACTOR_TYPE": "Bot", "ACTOR_LOGIN": "nebuko[bot]", "TRIGGER_COMMENT_HEADER": "<!-- bot-trigger:x -->"},
    ],
    ids=["workflow-continue", "bot-trigger"],
)
def test_guard3_defers_trusted_bot_continuation_to_engine(tmp_path, overrides):
    result = _run(tmp_path, CLAUDE_CODE_OAUTH_TOKEN_SECRET="tok", TRUSTED_BOTS=TRUSTED, **overrides)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "overrides",
    [
        {"ACTOR_TYPE": "Bot", "ACTOR_LOGIN": "evil[bot]", "TRIGGER_COMMENT_HEADER": CONTINUE_HEADER},
        {"ACTOR_TYPE": "Bot", "ACTOR_LOGIN": "nebuko[bot]", "TRIGGER_COMMENT_HEADER": "plain fresh run"},
        {
            "ACTOR_TYPE": "Bot",
            "ACTOR_LOGIN": "nebuko[bot]",
            "TRIGGER_COMMENT_HEADER": "intro\nmore\n> <!-- workflow-continue -->",
        },
        {"EVENT_NAME": "push", "ACTOR_TYPE": "Bot", "ACTOR_LOGIN": "nebuko[bot]", "TRIGGER_COMMENT_HEADER": CONTINUE_HEADER},
    ],
    ids=["untrusted-bot-forged-marker", "bot-no-marker", "quoted-marker-deep", "bot-non-comment-event"],
)
def test_guard3_refuses_untrusted_or_unmarked_bot_runs(tmp_path, overrides):
    result = _run(tmp_path, CLAUDE_CODE_OAUTH_TOKEN_SECRET="tok", TRUSTED_BOTS=TRUSTED, **overrides)
    assert result.returncode != 0
    assert "API-key lane" in result.stdout or "cannot start" in result.stdout


def test_guard3_does_not_block_non_human_actors_without_claude_token(tmp_path):
    result = _run(tmp_path, ACTOR_TYPE="Bot", ACTOR_LOGIN="dependabot[bot]")
    assert result.returncode == 0, result.stdout + result.stderr


def test_caller_pat_fast_path_gates_oidc_broker():
    doc = yaml.safe_load(WORKFLOW.read_text())
    for job in ("run-task", "agent-metrics"):
        steps = {s.get("id"): s for s in doc["jobs"][job]["steps"] if s.get("id")}
        assert steps["caller_pat"]["env"] == {"CALLER_PAT": "${{ secrets.GH_PAT }}"}
        assert steps["gh_token"]["if"] == "steps.caller_pat.outputs.present != 'true'"
