"""Deterministic issue-comment batch deletion (issue #644).

Human-only ``/workflow delete comments --issue <n> [--from <comment-id>]``: a
sole command executed outside agent scope — no agent, no LLM, no workflow
routing. It reads the resolver env contract, batch-deletes issue comments via
the ``gh`` CLI and writes ``final_message.txt`` so the normal posting pipeline
publishes the summary.

Deletion semantics:
* ``--from <id>`` present  -> delete every comment with ``id >= from_id``.
* ``--from`` absent        -> delete ALL comments on the issue.
* HTTP 204 -> deleted; HTTP 404 -> skipped (already gone, do NOT abort);
  any other failure -> record the error and STOP the batch (partial result).

Env contract: ``DELETE_COMMENTS_REPO`` (owner/repo, falls back to
``GITHUB_REPOSITORY``), ``DELETE_COMMENTS_ISSUE`` (int, required),
``DELETE_COMMENTS_FROM`` (int or empty). Requires GH_TOKEN with issues:write.
"""

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

_FINAL_MESSAGE_FILE = "final_message.txt"


@dataclass(frozen=True)
class DeleteCommentsResult:
    """Immutable outcome record of one batch deletion.

    ``from_id`` is None for a full-issue scope; ``skipped`` holds comment ids
    that were already gone (404); ``errors`` holds the non-404 failure message
    that stopped the batch (empty on a clean finish).
    """

    repo: str
    issue: int
    from_id: Optional[int]
    deleted: int
    skipped: Tuple[int, ...]
    errors: Tuple[str, ...]


# ── gh plumbing ──────────────────────────────────────────────────────


def _run(args: List[str]) -> subprocess.CompletedProcess:
    """Run a ``gh`` CLI command and return the CompletedProcess."""
    env = os.environ.copy()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        env["GH_TOKEN"] = token
    return subprocess.run(["gh"] + args, capture_output=True, text=True, env=env)


def _is_ascii_digits(value: str) -> bool:
    """True for non-empty ASCII-digit strings.

    ``str.isdigit`` also accepts non-ASCII digits (e.g. ``'²'``) that ``int()``
    rejects; restricting numeric input to ASCII prevents an ``int()`` crash.
    """
    return value.isascii() and value.isdigit()


def _is_404(stderr: str) -> bool:
    """True when gh reports an HTTP 404 (comment already gone)."""
    return "HTTP 404" in (stderr or "")


# ── Deletion ─────────────────────────────────────────────────────────


def list_comment_ids(repo: str, issue: int) -> List[int]:
    """List the issue's comment ids in ascending order (numeric only)."""
    # @stub-protected
    result = _run(
        [
            "api",
            "--paginate",
            "-H",
            "Accept:application/vnd.github+json",
            f"/repos/{repo}/issues/{issue}/comments",
            "--jq",
            ".[].id",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to list comments on {repo}#{issue}: {result.stderr.strip()}"
        )
    ids = [
        int(line.strip())
        for line in (result.stdout or "").splitlines()
        if _is_ascii_digits(line.strip())
    ]
    return sorted(ids)


def delete_comments_from(
    repo: str, issue: int, from_id: Optional[int]
) -> DeleteCommentsResult:
    """Delete comments on ``issue`` (``id >= from_id``, or all when None).

    Per comment: HTTP 204 -> ``deleted += 1``; HTTP 404 -> appended to
    ``skipped`` (already gone, batch continues); any other failure -> appended
    to ``errors`` and the batch STOPS.
    """
    # @stub-protected
    targets = [
        cid
        for cid in list_comment_ids(repo, issue)
        if from_id is None or cid >= from_id
    ]

    deleted = 0
    skipped: List[int] = []
    errors: List[str] = []
    for cid in targets:
        result = _run(
            ["api", "--method", "DELETE", f"/repos/{repo}/issues/comments/{cid}"]
        )
        if result.returncode == 0:
            deleted += 1
        elif _is_404(result.stderr):
            skipped.append(cid)
        else:
            errors.append(
                f"failed to delete comment {cid} on {repo}#{issue}: "
                f"{result.stderr.strip()}"
            )
            break

    return DeleteCommentsResult(
        repo=repo,
        issue=issue,
        from_id=from_id,
        deleted=deleted,
        skipped=tuple(skipped),
        errors=tuple(errors),
    )


# ── Summary rendering ────────────────────────────────────────────────


def render_summary(result: DeleteCommentsResult) -> str:
    """Render the batch outcome as the posted comment body."""
    scope = (
        f"from comment {result.from_id}"
        if result.from_id is not None
        else "all comments"
    )
    if result.errors:
        lines = [
            f"### 🧹 Delete comments — issue #{result.issue} (partial, stopped)",
            f"- Scope: {scope}",
            f"- Deleted: {result.deleted}",
            f"- Skipped (already gone): {len(result.skipped)}",
            f"- **Stopped on error:** {result.errors[0]}",
        ]
    else:
        lines = [
            f"### 🧹 Delete comments — issue #{result.issue}",
            f"- Scope: {scope}",
            f"- Deleted: {result.deleted} comment(s)",
            f"- Skipped (already gone): {len(result.skipped)}",
        ]
    return "\n".join(lines)


# ── Config & entry point ─────────────────────────────────────────────


def read_config(env) -> dict:
    """Read and validate the delete env contract. Raises ValueError."""
    repo = (
        env.get("DELETE_COMMENTS_REPO", "") or env.get("GITHUB_REPOSITORY", "")
    ).strip()
    if not repo:
        raise ValueError("DELETE_COMMENTS_REPO (or GITHUB_REPOSITORY) is required")
    issue_raw = (env.get("DELETE_COMMENTS_ISSUE", "") or "").strip()
    if not _is_ascii_digits(issue_raw):
        raise ValueError(
            f"DELETE_COMMENTS_ISSUE must be a positive integer, got {issue_raw!r}"
        )
    from_raw = (env.get("DELETE_COMMENTS_FROM", "") or "").strip()
    return {
        "repo": repo,
        "issue": int(issue_raw),
        "from_id": int(from_raw) if _is_ascii_digits(from_raw) else None,
    }


def write_final_message(message: str) -> None:
    """Write the posted body for the pipeline (``final_message.txt``)."""
    with open(_FINAL_MESSAGE_FILE, "w", encoding="utf-8") as f:
        f.write(message)


def main() -> int:
    """Run the deletion; returns the process exit code.

    A contract error is a *failure result*, not a crash: the failure summary
    is still written so the GHA job continues to posting.
    """
    # @stub-protected
    try:
        config = read_config(os.environ)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        write_final_message(f"### 🧹 Delete comments — FAILED\n- **Config error:** {exc}")
        return 1

    print(
        f"Deleting comments on {config['repo']}#{config['issue']} "
        f"(from_id={config['from_id']})",
        flush=True,
    )
    try:
        outcome = delete_comments_from(
            config["repo"], config["issue"], config["from_id"]
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        write_final_message(
            f"### 🧹 Delete comments — issue #{config['issue']}\n- **Failed:** {exc}"
        )
        return 0

    write_final_message(render_summary(outcome))
    print(
        f"Deleted {outcome.deleted}, skipped {len(outcome.skipped)}, "
        f"errors={len(outcome.errors)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
