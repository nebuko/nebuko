"""Contract for issue #1306: engine commits are attributed to ``nebuko-agent``.

The reusable workflow's token step names the git author. Attribution is metadata
only: the push keeps using the broker's ``nebuko[bot]`` installation token, so
comments still post as the App while commits link to the ``nebuko-agent``
account.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"
PATCHES = REPO_ROOT / "patches"

NAME = "nebuko-agent"
EMAIL = "350013676+nebuko-agent@users.noreply.github.com"

NAME_LINE = f'NEBUKO_GIT_AUTHOR_NAME=${{NEBUKO_GIT_AUTHOR_NAME:-{NAME}}}'
EMAIL_LINE = f"NEBUKO_GIT_AUTHOR_EMAIL=${{NEBUKO_GIT_AUTHOR_EMAIL:-{EMAIL}}}"

#: The reusable workflow has two token steps (implementation and review jobs).
TOKEN_STEPS = 2


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_commit_identity_names_nebuko_agent() -> None:
    """Given the workflow, when its token steps are inspected, then both
    default the git author to the nebuko-agent account (metadata only)."""
    text = _workflow()
    assert text.count(NAME_LINE) == TOKEN_STEPS
    assert text.count(EMAIL_LINE) == TOKEN_STEPS
    assert "331982189+nebuko[bot]@users.noreply.github.com" not in text


def test_push_token_stays_the_bot() -> None:
    """Attribution must not change the push credential: the broker token is
    still what lands in ``GH_TOKEN``."""
    text = _workflow()
    assert text.count('echo "GH_TOKEN=$TOKEN" >> "$GITHUB_ENV"') == TOKEN_STEPS
    assert text.count('if [ "$SOURCE" = "broker" ]; then') == TOKEN_STEPS


def test_no_stale_workflow_patch_is_shipped() -> None:
    """The identity ships in the workflow itself, so a patch against it would
    be a leftover a human could apply twice."""
    stale = [
        path.name
        for path in PATCHES.glob("*.patch")
        if ".github/workflows/nebuko-workflow.yaml" in path.read_text(encoding="utf-8")
    ]
    assert stale == []
