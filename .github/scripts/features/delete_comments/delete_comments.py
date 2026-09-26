"""Deterministic issue-comment batch deletion (issue #644).

Implements the human-only command ``/workflow delete comments --issue <n>
[--from <comment-id>]``. No agent, no LLM: it reads a resolver env contract
(like ``features/run_script/run_script.py``), batch-deletes issue comments via
the ``gh`` CLI, and feeds the workflow's normal posting pipeline (writes
``final_message.txt`` (+ split parts) and ``workflow_state.json``) so the
existing prepare -> bot-marker -> post chain runs untouched.

Deletion semantics (issue #644):
* ``--from <id>`` present  -> delete every comment with ``id >= from_id``.
* ``--from`` absent        -> delete ALL comments on the issue.
* HTTP 204 -> deleted; HTTP 404 -> skipped (already gone, do NOT abort);
  any other failure -> record the error and STOP the batch (partial result).

Usage (GHA): ``python3 "$SCRIPTS/features/delete_comments/delete_comments.py"``
with the resolver env contract: ``DELETE_COMMENTS_REPO`` (owner/repo, falls
back to ``GITHUB_REPOSITORY``), ``DELETE_COMMENTS_ISSUE`` (int, required),
``DELETE_COMMENTS_FROM`` (int or empty). Requires GH_TOKEN with issues:write.
"""

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_script.sub_domains.message_splitter.message_splitter import split_message
from config import parse_position_env
from workflow_state import Dispatch, build_state, extract_state_from_body, state_to_json

FINAL_MESSAGE_MAX_LEN = 10000
_FINAL_MESSAGE_FILE = "final_message.txt"
_STATE_FILE = "workflow_state.json"


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


# ── gh plumbing (mirrors github_client._gh_cli/_run/_resolve_token) ───


def _gh_cli() -> List[str]:
    """Return the gh CLI path."""
    return ["gh"]


def _resolve_token(token: Optional[str] = None) -> Optional[str]:
    """Resolve a GitHub token from an explicit param or the environment."""
    if token:
        return token
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _run(
    args: List[str],
    token: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run a ``gh`` CLI command and return the CompletedProcess."""
    env = os.environ.copy()
    resolved = _resolve_token(token)
    if resolved:
        env["GH_TOKEN"] = resolved
    return subprocess.run(
        _gh_cli() + args, capture_output=True, text=True, env=env
    )


# ── Deletion ─────────────────────────────────────────────────────────


def list_comment_ids(repo: str, issue: int, token: Optional[str] = None) -> List[int]:
    """List the issue's comment ids in ascending order (numeric only).

    Runs ``gh api --paginate /repos/{repo}/issues/{issue}/comments --jq
    '.[].id'``. Raises RuntimeError on a non-zero exit (e.g. auth/permission).
    """  # @stub-protected
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
        token=token,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to list comments on {repo}#{issue}: {result.stderr.strip()}"
        )
    ids: List[int] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if _is_ascii_digits(line):
            ids.append(int(line))
    return sorted(ids)


def delete_comments_from(
    repo: str,
    issue: int,
    from_id: Optional[int],
    token: Optional[str] = None,
) -> DeleteCommentsResult:
    """Delete comments on ``issue`` (``id >= from_id``, or all when None).

    Per comment: HTTP 204 -> ``deleted += 1``; HTTP 404 -> appended to
    ``skipped`` (already gone, batch continues); any other failure -> appended
    to ``errors`` and the batch STOPS. Returns the ``DeleteCommentsResult``.
    """  # @stub-protected
    ids = list_comment_ids(repo, issue, token=token)
    targets = [cid for cid in ids if from_id is None or cid >= from_id]

    deleted = 0
    skipped: List[int] = []
    errors: List[str] = []
    for cid in targets:
        result = _run(
            ["api", "--method", "DELETE", f"/repos/{repo}/issues/comments/{cid}"],
            token=token,
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


def _is_404(stderr: str) -> bool:
    """True when gh reports an HTTP 404 (comment already gone)."""
    return "HTTP 404" in (stderr or "")


def _is_ascii_digits(value: str) -> bool:
    """True for non-empty ASCII-digit strings.

    ``str.isdigit`` also accepts non-ASCII digits (e.g. ``'²'``) that ``int()``
    rejects; restricting numeric input to ASCII prevents an ``int()`` crash
    (issue #644 review).
    """
    return value.isascii() and value.isdigit()


# ── Summary rendering ────────────────────────────────────────────────


def render_summary(result: DeleteCommentsResult) -> str:
    """Render the batch outcome as the posted comment body."""
    scope = f"from comment {result.from_id}" if result.from_id is not None else "all comments"
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


# ── Config & artifacts (mirrors run_script conventions) ──────────────


def _state_config(env) -> dict:
    """State-pipeline fields, resolved without validation.

    Used to write artifacts even when the env contract is invalid, so the GHA
    posting pipeline still surfaces a failure comment (issue #644 review).
    """
    return {
        "trigger_body": env.get("TRIGGER_BODY", "") or "",
        "workflow": env.get("WORKFLOW_NAME", "") or "",
        "step": env.get("WORKFLOW_STEP", "") or "",
    }


def read_config(env) -> dict:
    """Read and validate the delete env contract. Raises ValueError."""
    repo = (env.get("DELETE_COMMENTS_REPO", "") or env.get("GITHUB_REPOSITORY", "")).strip()
    if not repo:
        raise ValueError("DELETE_COMMENTS_REPO (or GITHUB_REPOSITORY) is required")
    issue_raw = (env.get("DELETE_COMMENTS_ISSUE", "") or "").strip()
    if not _is_ascii_digits(issue_raw):
        raise ValueError(f"DELETE_COMMENTS_ISSUE must be a positive integer, got {issue_raw!r}")
    from_raw = (env.get("DELETE_COMMENTS_FROM", "") or "").strip()
    from_id: Optional[int] = int(from_raw) if _is_ascii_digits(from_raw) else None
    return {
        "repo": repo,
        "issue": int(issue_raw),
        "from_id": from_id,
        **_state_config(env),
    }


def write_final_message(message: str) -> int:
    """Write final_message.txt (part 1) + overflow parts; returns part count."""
    parts = split_message(message, FINAL_MESSAGE_MAX_LEN)
    if not parts:
        parts = [""]
    with open(_FINAL_MESSAGE_FILE, "w", encoding="utf-8") as f:
        f.write(parts[0])
    for i, part in enumerate(parts[1:], start=2):
        with open(f"final_message_part_{i}.txt", "w", encoding="utf-8") as f:
            f.write(part)
    if len(parts) > 1:
        with open("final_message_parts_count.txt", "w", encoding="utf-8") as f:
            f.write(str(len(parts)))
    return len(parts)


def write_workflow_state(config: dict, result: str) -> dict:
    """Advance the workflow state with a synthetic pass/fail dispatch.

    Mirrors ``run_script.write_workflow_state`` so the posting pipeline (which
    embeds the collapsed state section and the continue token) runs unchanged.
    """
    prev_state = extract_state_from_body(config["trigger_body"])
    summary = (
        f"Delete-comments step finished ({result}); "
        "output posted as a comment."
    )
    new_state = build_state(
        prev_state,
        workflow=config["workflow"],
        step=config["step"],
        agent="script",
        summary=summary,
        comment_ids=[],
        result=result,
        dispatch=Dispatch(status="advance", branch=result),
        position=parse_position_env(os.environ.get("WORKFLOW_POSITION", "")),
    )
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(state_to_json(new_state))
    return new_state.model_dump(mode="json", exclude_defaults=True)


# ── Entry point ──────────────────────────────────────────────────────


def main() -> int:
    """Run the deletion; returns the process exit code (0 on a posted result).

    Follows the ``run_script`` failure-result convention: a contract error or a
    batch error is a *failure result*, not a crash — the summary artifact is
    still written so the GHA job continues to posting.
    """  # @stub-protected
    try:
        config = read_config(os.environ)
    except ValueError as exc:
        # A contract error is still a *result*, not a crash: write the failure
        # artifacts so the posting pipeline surfaces it on the issue
        # (issue #644 review).
        print(f"ERROR: {exc}", file=sys.stderr)
        message = f"### 🧹 Delete comments — FAILED\n- **Config error:** {exc}"
        write_final_message(message)
        write_workflow_state(_state_config(os.environ), "fail")
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
        # Listing failed (auth/permission/network): a clear failure result.
        print(f"ERROR: {exc}", file=sys.stderr)
        message = f"### 🧹 Delete comments — issue #{config['issue']}\n- **Failed:** {exc}"
        write_final_message(message)
        write_workflow_state(config, "fail")
        return 0

    result = "pass" if not outcome.errors else "fail"
    message = render_summary(outcome)
    n_parts = write_final_message(message)
    if n_parts > 1:
        print(f"Comment split into {n_parts} parts", flush=True)
    write_workflow_state(config, result)
    print(
        f"Deleted {outcome.deleted}, skipped {len(outcome.skipped)}, "
        f"errors={len(outcome.errors)} -> {result}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
