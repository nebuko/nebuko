"""Deterministic issue-comment deletion (issue #644, simplified #674).

Human-only ``/nebuko delete comments --issue <n>``: a sole command executed
outside agent scope — no agent, no LLM, no workflow routing, no comment
posting. It deletes every comment on the target issue via the ``gh`` CLI and
nothing else.

Deletion semantics:
* Every comment on the issue is deleted.
* HTTP 404 -> the comment is already gone, so it is skipped and the batch
  continues; any other failure -> raise (the CLI maps it to a non-zero exit).

Env contract: ``DELETE_COMMENTS_REPO`` (owner/repo, falls back to
``GITHUB_REPOSITORY``), ``DELETE_COMMENTS_ISSUE`` (int, required). Requires
GH_TOKEN (or GITHUB_TOKEN) with issues:write.
"""

import os
import subprocess
import sys
from typing import List, Optional


# ── gh plumbing ──────────────────────────────────────────────────────


def _run(
    args: List[str], token: Optional[str] = None
) -> subprocess.CompletedProcess:
    """Run a ``gh`` CLI command and return the CompletedProcess."""
    env = os.environ.copy()
    resolved = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if resolved:
        env["GH_TOKEN"] = resolved
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


def list_comment_ids(
    repo: str, issue: int, token: Optional[str] = None
) -> List[int]:
    """List the issue's comment ids in ascending order (numeric only)."""
    result = _run(
        [
            "api",
            "--paginate",
            "-H",
            "Accept:application/vnd.github+json",
            f"/repos/{repo}/issues/{issue}/comments",
            "--jq",
            ".[].id",
        ],
        token,
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


def delete_issue_comments(repo: str, issue_number: int, token: str) -> None:
    """Delete all comments on the given issue via the GitHub API.

    Raises on failure. The CLI layer maps exceptions to a non-zero exit code
    and success to ``0``. This is the entire command: no agent, no workflow
    routing, no comment posting.
    """
    # @stub-protected: signature defined by architect — do not modify
    for cid in list_comment_ids(repo, issue_number, token):
        result = _run(
            ["api", "--method", "DELETE", f"/repos/{repo}/issues/comments/{cid}"],
            token,
        )
        if result.returncode == 0 or _is_404(result.stderr):
            continue
        raise RuntimeError(
            f"failed to delete comment {cid} on {repo}#{issue_number}: "
            f"{result.stderr.strip()}"
        )


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
    return {"repo": repo, "issue": int(issue_raw)}


def _auth_token(env) -> str:
    """The token to authenticate the delete calls with."""
    return (env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or "").strip()


def main() -> int:
    """Delete the target issue's comments; return the process exit code.

    Pure CLI action: ``0`` on success, ``1`` on a contract or API failure. No
    comment is posted and no workflow state is read or written.
    """
    try:
        config = read_config(os.environ)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"Deleting all comments on {config['repo']}#{config['issue']}",
        flush=True,
    )
    try:
        delete_issue_comments(
            config["repo"], config["issue"], _auth_token(os.environ)
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
