"""Deliver ``.github/workflows/*`` changes as a patch, never as a push.

The agent's token deliberately has no ``workflow`` scope (``AGENTS.md``), so
GitHub rejects any push that carries a workflow file:

    ! [remote rejected] ... (refusing to allow a Personal Access Token to
      create or update workflow `.github/workflows/internal-tests.yaml`
      without `workflow` scope)

Before this module the whole ``ensure_pr`` fallback failed on such a diff and
the work existed only on the runner (issue #1080). Here the delta is split by
path: workflow files become a ``git apply``-able patch a human applies, the
rest is published normally.

Nothing in this module pushes, commits or stages anything — it only *builds*
the patch, renders the comment and reverts the workflow side once delivery
succeeded. Splitting the diff is what guarantees the ``git push`` call can
never carry a workflow path, so no reject-text parsing is needed.
"""

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from config.sub_domains.comment_size.comment_size import DEFAULT_MAX_COMMENT_CHARS
from nebuko.engine.lib.git.idempotency import GitIdempotency

#: Everything under this repo-relative prefix is patch-only, never pushed.
WORKFLOW_DIR = ".github/workflows/"

#: File name of the delivered patch, both for the apply command and artifact.
PATCH_FILENAME = "workflow-patch.diff"

#: Directory (under ``$RUNNER_TEMP``) the engine stages run artifacts in.
#: Mirrors ``adapters.github.ARTIFACT_DIR_NAME``.
PATCH_ARTIFACT_DIR = "nebuko-task-logs"

#: The one-line command a human runs after saving the patch.
APPLY_COMMAND = f"git apply {PATCH_FILENAME}"


# @stub-protected
def patch_is_already_applied(patch_text: str, repo_root: str) -> bool:
    """Whether the patch content is already present in the checkout (issue #1194).

    Wraps :class:`GitIdempotency.patch_already_applied` (a reverse-apply
    probe) so the delivery can say "already applied" instead of re-issuing
    the apply command for a change a human already applied.
    """
    raise NotImplementedError


# @stub-protected
def is_workflow_path(path: str) -> bool:
    """Whether a repo-relative path is a GitHub Actions workflow file.

    Prefix match, so ``.github/workflows/ci.yaml`` counts while lookalikes
    such as ``docs/workflows.md`` or ``.github/workflow-docs/x`` do not.
    """
    normalized = path.strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.startswith(WORKFLOW_DIR)


# @stub-protected
def split_paths(paths: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition paths into ``(workflow_paths, plain_paths)``, order-preserving."""
    workflow: list[str] = []
    plain: list[str] = []
    for path in paths:
        (workflow if is_workflow_path(path) else plain).append(path)
    return tuple(workflow), tuple(plain)


@dataclass(frozen=True)
class PatchComment:
    """A rendered patch delivery.

    Fields:
        body: The comment body (fenced diff, or a link when oversized).
        workflow_paths: The workflow paths the patch carries.
        oversized: True when the diff did not fit and only the artifact is
            linked.
        artifact_name: The staged artifact name when one is referenced.
    """

    # @stub-protected: signature defined by architect — do not modify
    body: str
    workflow_paths: tuple[str, ...]
    oversized: bool
    artifact_name: str = ""


# @stub-protected
def build_patch(
    *,
    base_ref: str,
    workflow_paths: Sequence[str],
    run_git: Callable[..., str],
) -> str:
    """The ``git diff`` that restores the workflow delta onto ``base_ref``.

    Binary and untracked workflow files are included (``--binary`` plus an
    intent-to-add registration first) so ``git apply`` on a clean checkout of
    ``base_ref`` reproduces the change exactly, paths and modes included.
    """
    paths = list(workflow_paths)
    if not paths:
        return ""
    try:
        run_git(["add", "--intent-to-add", "--"] + paths)
    except RuntimeError:
        # Already tracked (or git refuses the intent-to-add for another
        # reason): the diff below still covers modified and deleted files.
        pass
    return run_git(["diff", "--binary", base_ref, "--"] + paths)


def _patch_notice(workflow_paths: Sequence[str], apply_lines: str) -> list[str]:
    listed = "\n".join(f"- `{path}`" for path in workflow_paths)
    return [
        "### ⚠️ Workflow change delivered as a patch",
        "",
        "The agent token has no `workflow` scope, so GitHub refuses to push "
        "`.github/workflows/*`. These files were therefore **not** pushed — a "
        "human must apply them:",
        "",
        listed,
        "",
        apply_lines,
    ]


# @stub-protected
def render_patch_comment(
    *,
    diff: str,
    workflow_paths: Sequence[str],
    base_name: str,
    max_chars: int = DEFAULT_MAX_COMMENT_CHARS,
    already_applied: bool = False,
) -> PatchComment:
    """Render the patch comment body, or an artifact link when it is too big.

    Over ``max_chars`` an inline fence would be rejected by GitHub (65,536
    hard cap), so the diff stays out of the comment and the staged artifact is
    linked instead. With ``already_applied`` the diff is informational
    only: the change is already in the tree, so no apply command is
    offered (issue #1194).
    """
    inline_lines = [
        "```diff",
        diff.strip("\n"),
        "```",
        "",
        f"Apply on a clean checkout of `{base_name}`:",
        "",
        "```bash",
        f"# save the block above as {PATCH_FILENAME}, then",
        APPLY_COMMAND,
        "```",
    ]
    body = "\n".join(
        _patch_notice(workflow_paths, f"Apply with `{APPLY_COMMAND}` (see below).")
        + [""]
        + inline_lines
    )
    if len(body) <= max_chars:
        return PatchComment(
            body=body, workflow_paths=tuple(workflow_paths), oversized=False
        )

    artifact_lines = [
        "The diff is too large to inline in a GitHub comment, so the engine "
        f"staged it as the run artifact **`{PATCH_FILENAME}`**.",
        "",
        f"1. Download `{PATCH_FILENAME}` from this run's artifacts.",
        f"2. Save it next to your checkout and run `{APPLY_COMMAND}` on a clean "
        f"checkout of `{base_name}`.",
    ]
    body = "\n".join(
        _patch_notice(
            workflow_paths, f"Apply with `{APPLY_COMMAND}` (see below)."
        )
        + [""]
        + artifact_lines
    )
    return PatchComment(
        body=body,
        workflow_paths=tuple(workflow_paths),
        oversized=True,
        artifact_name=PATCH_FILENAME,
    )


# @stub-protected
def stage_patch_artifact(diff: str) -> str:
    """Write the patch under ``$RUNNER_TEMP`` for the workflow's upload step.

    Returns the artifact name (also the file name, so the upload step and the
    comment agree). Raises ``OSError`` when the file cannot be written — the
    caller must not treat an undelivered patch as delivered.
    """
    base = os.environ.get("RUNNER_TEMP", "").strip() or "/tmp"
    directory = os.path.join(base, PATCH_ARTIFACT_DIR)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, PATCH_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(diff if diff.endswith("\n") else diff + "\n")
    return PATCH_FILENAME


# @stub-protected
def revert_workflow_changes(
    *,
    base_ref: str,
    workflow_paths: Sequence[str],
    run_git: Callable[..., str],
) -> None:
    """Drop the workflow delta so the tree is clean for the guard.

    Tracked files are restored from ``base_ref`` (staging and worktree);
    untracked ones are removed. Call **only** after the patch is delivered —
    this discards the local copy.
    """
    paths = list(workflow_paths)
    if not paths:
        return
    try:
        run_git(["restore", "--source", base_ref, "--staged", "--worktree", "--"] + paths)
    except RuntimeError:
        try:
            run_git(["restore", "--staged", "--worktree", "--"] + paths)
        except RuntimeError:
            pass
    try:
        run_git(["clean", "-f", "--"] + paths)
    except RuntimeError:
        pass
