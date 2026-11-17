"""The comment trigger forwards the Claude Code token (issue #1389).

``nebuko-workflow.yaml`` declares, masks and exports ``CLAUDE_CODE_OAUTH_TOKEN``,
but a ``workflow_call`` only receives the secrets its caller names — so the
trigger workflow has to pass it, or a comment-triggered claude-code run starts
without a credential. Secret values travel through ``secrets:`` only, never
``with:`` (ADR-0008).

The trigger workflow change is delivered as a ``workflow-patch.diff`` (AGENTS.md:
the agent token has no ``workflow`` scope), so it is applied in memory when the
patch is present.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_PATH = Path(".github/workflows/interntal-trigger-nebuko-on-comment.yaml")
REUSABLE_PATH = Path(".github/workflows/nebuko-workflow.yaml")
PATCH_PATH = REPO_ROOT / "nebuko-task-logs" / "workflow-patch.diff"


def _patch_section(diff: str, path: Path) -> str:
    """The ``diff --git`` section of *diff* that targets *path*, if any."""
    target = f"b/{path.as_posix()}"
    sections: list[list[str]] = []
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            sections.append([raw])
        elif sections:
            sections[-1].append(raw)
    for section in sections:
        if section[0].split()[-1] == target:
            return "\n".join(section) + "\n"
    return ""


def _apply_unified_diff(text: str, diff: str) -> str:
    """Apply a small unified diff to *text* in memory (context must match)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    pos = 0
    in_hunk = False
    for raw in diff.splitlines():
        m = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", raw)
        if m:
            in_hunk = True
            start = int(m.group(1)) - 1
            out.extend(lines[pos:start])
            pos = start
            continue
        if not in_hunk or raw.startswith("diff ") or raw.startswith("index "):
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


def _patch_additions(section: str) -> list[str]:
    return [
        raw[1:]
        for raw in section.splitlines()
        if raw.startswith("+") and not raw.startswith("+++")
    ]


def _effective_text(path: Path) -> str:
    """The file, with its pending patch section applied unless already present.

    The trigger workflow change travels as a ``workflow-patch.diff`` until a
    human applies it, so the assertion has to see both sides: red if the change
    is lost from the file *and* the patch, green on either side of the apply.
    """
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    if PATCH_PATH.exists():
        section = _patch_section(PATCH_PATH.read_text(encoding="utf-8"), path)
        additions = _patch_additions(section)
        if additions and not all(line in text for line in additions):
            text = _apply_unified_diff(text, section)
    return text


def _workflow(path: Path) -> dict:
    return yaml.safe_load(_effective_text(path))


def test_trigger_forwards_the_claude_code_token() -> None:
    job = _workflow(TRIGGER_PATH)["jobs"]["agent"]
    assert (
        job["secrets"]["CLAUDE_CODE_OAUTH_TOKEN"]
        == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    )


def test_trigger_job_still_passes_the_provider_keys() -> None:
    job = _workflow(TRIGGER_PATH)["jobs"]["agent"]
    assert job["secrets"]["MISTRAL_API_KEY"] == "${{ secrets.MISTRAL_API_KEY }}"
    assert (
        job["secrets"]["CHEAPERINFERENCE_API_KEY"]
        == "${{ secrets.CHEAPERINFERENCE_API_KEY }}"
    )


def test_reusable_workflow_declares_it_optional() -> None:
    secrets = _workflow(REUSABLE_PATH)[True]["workflow_call"]["secrets"]
    assert secrets["CLAUDE_CODE_OAUTH_TOKEN"]["required"] is False


def test_reusable_workflow_exports_it_only_when_non_empty() -> None:
    # An empty export would shadow a token the runner already provides.
    steps = _workflow(REUSABLE_PATH)["jobs"]["run-task"]["steps"]
    step = next(s for s in steps if s.get("name") == "Export Claude Code token")
    assert "CLAUDE_CODE_OAUTH_TOKEN_SECRET" in step["env"]
    assert "::add-mask::" in step["run"]
    assert 'if [ -n "$CLAUDE_CODE_OAUTH_TOKEN_SECRET" ]' in step["run"]
