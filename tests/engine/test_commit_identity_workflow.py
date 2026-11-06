"""Contract for issue #1306: engine commits are attributed to ``nebuko-agent``.

The reusable workflow's token step names the git author. The change lives in
``patches/`` because the agent token cannot push ``.github/workflows/*``; these
tests pin the patch and apply it in memory so the contract is checked against
the workflow it will land on.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"
PATCH = REPO_ROOT / "patches" / "0002-issue-1306-nebuko-agent-commit-identity.patch"

NAME = "nebuko-agent"
EMAIL = "350013676+nebuko-agent@users.noreply.github.com"


def _apply(diff: str, text: str) -> str:
    """Apply a unified diff to ``text`` (context lines must match)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    pos = 0
    in_hunk = False
    for raw in diff.splitlines():
        header = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", raw)
        if header:
            in_hunk = True
            start = int(header.group(1)) - 1
            out.extend(lines[pos:start])
            pos = start
            continue
        if not in_hunk or raw.startswith(("diff ", "index ")):
            continue
        if raw.startswith("+"):
            out.append(raw[1:] + "\n")
        elif raw.startswith("-"):
            pos += 1
        elif not raw.startswith("\\"):
            out.append(lines[pos])
            pos += 1
    out.extend(lines[pos:])
    return "".join(out)


def _patched_workflow() -> str:
    return _apply(PATCH.read_text(encoding="utf-8"), WORKFLOW.read_text(encoding="utf-8"))


def test_patch_changes_only_the_commit_identity() -> None:
    diff = PATCH.read_text(encoding="utf-8")
    changed = [
        line[1:]
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert changed, "the patch carries no change"
    for line in changed:
        stripped = line.strip()
        assert "NEBUKO_GIT_AUTHOR_" in line or stripped.startswith("#"), (
            f"unrelated change in the patch: {line}"
        )


def test_commit_identity_names_nebuko_agent() -> None:
    """Given the patched workflow, when its token steps are inspected, then
    both default the git author to the nebuko-agent account (metadata only)."""
    text = _patched_workflow()
    assert f'NEBUKO_GIT_AUTHOR_NAME=${{NEBUKO_GIT_AUTHOR_NAME:-{NAME}}}' in text
    assert f"NEBUKO_GIT_AUTHOR_EMAIL=${{NEBUKO_GIT_AUTHOR_EMAIL:-{EMAIL}}}" in text
    assert "331982189+nebuko[bot]@users.noreply.github.com" not in text


def test_push_token_stays_the_bot() -> None:
    """Attribution must not change the push credential: the broker token is
    still what lands in ``GH_TOKEN``."""
    text = _patched_workflow()
    assert 'echo "GH_TOKEN=$TOKEN" >> "$GITHUB_ENV"' in text
    assert 'if [ "$SOURCE" = "broker" ]; then' in text
