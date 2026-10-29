"""Rendering contract for ``nebuko init`` (issue #991).

The generated caller workflow is the artifact the client commits, so its pin,
its config path, its runner label and its secrets block are pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.init.ports import InitAnswers, SetupTarget
from nebuko.engine.init.providers import PROVIDER_PROFILES, provider_by_name
from nebuko.engine.init.setup import (
    AGENT_CONFIG_PATH,
    WORKFLOW_PATH,
    next_steps_message,
    render_files,
)
from nebuko.engine.init.sources import FileTemplateSource

REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET = SetupTarget(host="github.com", owner="acme", repo="widgets")


def _answers(provider_name: str, runs_on: str = "ubuntu-latest") -> InitAnswers:
    return InitAnswers(
        target=TARGET,
        provider=provider_by_name(provider_name),
        runs_on=runs_on,
        pin_ref="v1.2.3",
    )


def _render(provider_name: str, runs_on: str = "ubuntu-latest"):
    return render_files(_answers(provider_name, runs_on), FileTemplateSource(REPO_ROOT))


def test_paths_are_the_documented_ones():
    files = _render("openrouter")
    assert files.agent_config_path == AGENT_CONFIG_PATH == ".nebuko/agents/agent-config.yaml"
    assert files.workflow_path == WORKFLOW_PATH == ".github/workflows/nebuko.yaml"


def test_agent_config_is_the_provider_template():
    files = _render("commandcode")
    expected = (REPO_ROOT / "defaults/agents/agent-config-commandcode.yaml").read_text(
        encoding="utf-8"
    )
    assert files.agent_config == expected


def test_workflow_pins_the_ref_and_never_main():
    workflow = _render("openrouter").workflow
    assert "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@v1.2.3" in workflow
    assert "@main" not in workflow


def test_workflow_points_at_the_generated_config_and_runner_label():
    workflow = _render("openrouter", runs_on="self-hosted").workflow
    assert 'agent_config_path: ".nebuko/agents/agent-config.yaml"' in workflow
    assert 'runs_on: "self-hosted"' in workflow


def test_no_placeholder_survives_rendering():
    workflow = _render("openrouter").workflow
    for placeholder in ("${pin_ref}", "${agent_config_path}", "${runs_on}", "${secrets_block}"):
        assert placeholder not in workflow


@pytest.mark.parametrize("profile", PROVIDER_PROFILES, ids=lambda p: p.name)
def test_secrets_block_has_only_the_provider_key_plus_gh_pat(profile):
    workflow = _render(profile.name).workflow
    for name in profile.secret_names:
        assert f"      {name}: ${{{{ secrets.{name} }}}}" in workflow
    assert "      GH_PAT: ${{ secrets.GH_PAT }}" in workflow
    others = {
        name
        for other in PROVIDER_PROFILES
        if other.name != profile.name
        for name in other.secret_names
    }
    for name in others:
        assert name not in workflow


def test_workflow_is_valid_yaml_with_the_expected_job():
    import yaml

    document = yaml.safe_load(_render("opencode").workflow)
    job = document["jobs"]["agent"]
    assert job["uses"].endswith("nebuko-workflow.yaml@v1.2.3")
    assert job["secrets"] == {
        "OPENCODE_GO_API_KEY": "${{ secrets.OPENCODE_GO_API_KEY }}",
        "GH_PAT": "${{ secrets.GH_PAT }}",
    }


def test_next_steps_message_names_the_pull_request_and_the_slug():
    message = next_steps_message("https://github.com/acme/widgets/pull/7", "acme/widgets")
    assert "Next steps" in message
    assert "1. Review and merge the pull request: https://github.com/acme/widgets/pull/7" in message
    assert 'comment "@@dev Hello Nebuko!" on an issue in acme/widgets.' in message
    assert message.endswith("Let's build something with Nebuko's agent teams 🤝")


def test_caller_workflow_grants_id_token_write():
    """Rendered workflow contains 'id-token: write' in the permissions block."""
    workflow = _render("openrouter").workflow
    permissions = workflow.split("permissions:", 1)[1].split("jobs:", 1)[0]
    assert "  id-token: write" in permissions


def test_caller_workflow_gates_comment_author_association():
    """Rendered gate checks OWNER/MEMBER/COLLABORATOR on author_association."""
    workflow = _render("openrouter").workflow
    assert "contains(fromJSON('[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]')" in workflow
    assert "github.event.comment.author_association" in workflow


def test_caller_workflow_ignores_nebuko_bot_comments():
    """Rendered gate excludes comments whose user.login == 'nebuko[bot]'."""
    workflow = _render("openrouter").workflow
    assert "github.event.comment.user.login != 'nebuko[bot]'" in workflow


def test_caller_workflow_gate_passes_issue_events():
    """Rendered gate short-circuits on non-issue_comment events."""
    workflow = _render("openrouter").workflow
    assert "github.event_name != 'issue_comment' ||" in workflow
