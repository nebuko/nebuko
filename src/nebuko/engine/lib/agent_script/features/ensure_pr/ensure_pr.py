"""Deterministic PR fallback — commit, push, and open/update a PR.

Guarantees the "checkpoint the work in a PR" requirement of graceful
interruption (issue #489) even when the wind-down LLM run is uncooperative:

  1. Split the delta by path — ``.github/workflows/*`` is patch-only.
  2. Commit the non-workflow working-tree changes (if any).
  3. Push the current branch to origin.
  4. Open a PR if none exists for the branch (idempotent).
  5. Deliver the workflow delta as a patch comment (issue #1080).

The split (step 1/5) exists because the agent's token has no ``workflow``
scope: any push carrying ``.github/workflows/*`` is rejected by GitHub, which
used to fail the whole fallback and lose the work with the runner. A human
applies the patch instead — see ``workflow_patch.py``.

Built on the same ``subprocess`` git pattern as ``remote_workflow._run_git``
and the ``gh`` CLI helpers in ``github_client.py``. Every ``gh`` call rides
:class:`github_client.GitHubTransport` so PR creation shares the same write
pacing, failure classification and bounded retries as comment publishing — a
content-creation secondary limit can no longer silently drop the PR (issue #805).
"""

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from subprocess import TimeoutExpired
from typing import Optional

from agent_script.features.ensure_pr.workflow_patch import (
    APPLY_COMMAND,
    PATCH_ARTIFACT_DIR,
    PATCH_FILENAME,
    build_patch,
    is_workflow_path,
    render_patch_comment,
    revert_workflow_changes,
    split_paths,
    stage_patch_artifact,
)
from github_client import GitHubTransport, ensure_bot_marker
from openhands.sdk import get_logger

from nebuko.engine.lib.git.git_session import GIT_IDENTITY_EMAIL, GIT_IDENTITY_NAME

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 120

#: Working-tree paths the engine itself writes into the checkout. They are not
#: agent output, so they never make the tree "dirty" for publication purposes.
#: Kept as a literal (not imported) because ``handoff_store`` imports this
#: module — it mirrors ``handoff_store.HANDOFF_RELATIVE_PATH``.
ENGINE_OWNED_PATHS = frozenset({".nebuko/handoff.md"})


def _repo_root() -> Optional[str]:
    """Checkout the git/gh commands must run in, or ``None`` for the process cwd.

    The in-process engine host runs the whole assembly under a temporary cwd
    (issue #740), so a bare ``git remote`` fails with "not a git repository".
    ``GFA_REPO_ROOT`` — exported by the engine's agent seam — pins git/gh to the
    real checkout so the wind-down can still commit, push and open the PR
    (issue #766).
    """
    root = os.environ.get("GFA_REPO_ROOT", "").strip()
    return root or None


def _run_git(
    args: list[str], timeout: int = DEFAULT_TIMEOUT, cwd: Optional[str] = None
) -> str:
    """Run a git command via subprocess with a timeout. Returns stdout."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd if cwd is not None else _repo_root(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _run_gh(
    args: list[str],
    timeout: int = DEFAULT_TIMEOUT,
    cwd: Optional[str] = None,
    *,
    mutative: bool = False,
    transport: Optional[GitHubTransport] = None,
) -> str:
    """Run a gh CLI command through the rate-limit-aware transport.

    Routing every ``gh`` call through :class:`GitHubTransport` gives PR
    creation the same write pacing, failure classification and bounded retries
    as comment publishing, so a content-creation secondary limit can no longer
    silently drop the PR (issue #805). Raises ``RuntimeError`` when the call
    cannot be delivered after the transport's retries.
    """
    transport = transport or GitHubTransport()
    result = transport.call(
        args,
        mutative=mutative,
        cwd=cwd if cwd is not None else _repo_root(),
        timeout=timeout,
    )
    if not result.ok:
        raise RuntimeError(
            f"gh {' '.join(args)} failed ({result.status}): {result.detail}"
        )
    return result.payload.strip()


@dataclass(frozen=True)
class EnsurePrResult:
    """Immutable outcome of the deterministic PR fallback."""

    skipped: bool = False
    error: bool = False
    commit_hash: str = ""
    pushed: bool = False
    pr_url: str = ""
    message: str = ""
    # @stub-protected: signature defined by architect — do not modify
    patch_comment_url: str = ""
    patch_paths: tuple[str, ...] = ()
    patch_artifact: str = ""


@dataclass(frozen=True)
class PublishSplit:
    """How the delta splits between the push and the delivered patch.

    Fields:
        workflow_paths: ``.github/workflows/*`` paths — patch-only, never
            staged, committed or pushed.
        plain_paths: Everything else — staged/committed/pushed as today.
        republished: True when unpushed commits already carried workflow
            files and the publish commit had to be rebuilt.
    """

    # @stub-protected: signature defined by architect — do not modify
    workflow_paths: tuple[str, ...]
    plain_paths: tuple[str, ...]
    republished: bool


def _current_branch() -> str:
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"])


def _default_remote() -> Optional[str]:
    """Return the default push remote name, or None if remotes are missing."""
    out = _run_git(["remote"]).strip()
    return out.splitlines()[0].strip() if out else None


def _has_uncommitted_changes() -> bool:
    return bool(_run_git(["status", "--porcelain"]).strip())


def _unstage_workflow_paths(paths: Sequence[str]) -> None:
    """Drop ``.github/workflows/*`` paths from the index, keeping the worktree.

    ``git reset --mixed`` preserves the *index* as it was, so a workflow file
    the agent had already staged would still be carried into the next commit
    even though ``_commit_pending_changes`` stages nothing for it. A partial
    ``git restore --staged`` (no ``--worktree``) clears that landmine and
    leaves the change in the working tree, where the patch flow delivers it
    (issue #1080).
    """
    if not paths:
        return
    try:
        _run_git(["restore", "--staged", "--", *paths])
    except RuntimeError as exc:
        # An unstaged/untracked path is not in the index at all — nothing to
        # scrub. Anything else is logged; the push is still workflow-free
        # because no commit command names a workflow path.
        logger.warning(f"ensure_pr: could not scrub the index: {exc}")


def _status_entries() -> tuple[tuple[str, str], ...]:
    """Parse ``git status --porcelain -z`` into ``(path, code)`` pairs."""
    entries: list[tuple[str, str]] = []
    # ``-z`` is the raw format: ``XY<path>`` with *no* separator between the
    # two status columns and the path, and NUL (not newline) terminators. The
    # separator-less layout matters: the historic ``line[3:]`` slice on the
    # newline form silently ate the leading character of a path that begins
    # with ``.`` — i.e. ``.github/workflows/*``, the very paths this flow
    # must classify (issue #1080).
    # ``-uall``: plain ``-z`` collapses every untracked file inside a new
    # directory into the directory itself (``?? .github/``), which hid brand-new
    # workflow files from the patch flow and let them ride into a plain commit.
    raw = _run_git(["status", "--porcelain", "-z", "-uall"])
    fields = raw.split("\0")
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 3:
            continue
        code, path = entry[:2], entry[2:]
        # Some git versions keep the historic space after the code in the
        # ``-z`` form; trim it so the path can be handed straight back to git.
        path = path.lstrip(" ") if path.startswith(" ") else path
        entries.append((path, code))
        if code[0] in ("R", "C"):
            # A rename/copy is two NUL-separated fields — ``R  new\0old\0`` —
            # with no `` -> `` in the raw form. The origin is its own field and
            # carries **no** status prefix, so it is taken whole; reading it as
            # a status entry would eat its first character and classify the
            # workflow the rename came *from* as plain work (issue #1080).
            if index >= len(fields):
                continue
            origin_path = fields[index]
            index += 1
            if not origin_path:
                continue
            entries.append((origin_path, code))
    return tuple(entries)


def _commit_pending_changes(paths: Sequence[str] | None = None) -> str:
    """Stage and commit untracked/modified tracked files. Returns the hash.

    ``paths`` restricts the commit to exactly those paths; it must never
    contain a ``.github/workflows/*`` path, because committing one would only
    make the next ``git push`` fail (issue #1080). ``None`` keeps the historic
    "everything git reports" behaviour.
    """
    # Scope staging to untracked + modified tracked files only; deletions,
    # type changes and renames are left for the next run to handle in
    # context (keeps the automatic checkpoint conservative).
    allowed = None if paths is None else set(paths)
    staged: list[str] = []
    for path, code in _status_entries():
        if allowed is not None and path not in allowed:
            continue
        if code == "??" or " M" in code or "M " in code:
            staged.append(path)
    if staged:
        _run_git(["add", "--"] + staged)
    _run_git(
        [
            "-c",
            f"user.name={GIT_IDENTITY_NAME}",
            "-c",
            f"user.email={GIT_IDENTITY_EMAIL}",
            "commit",
            "-m",
            "WIP checkpoint — automatic commit from graceful agent interruption\n\n"
            "The agent hit its soft time budget. This checkpoint preserves the "
            "in-flight work so the next run can resume instead of starting over.\n\n"
            "(auto-committed by the ensure_pr fallback, issue #489)",
        ]
    )
    return _run_git(["rev-parse", "HEAD"])


def _changed_paths(compare_ref: str) -> tuple[str, ...]:
    """Paths differing between ``compare_ref`` and HEAD.

    Raises when the comparison itself fails (an unknown ref is not "no
    changes") so a broken base can never masquerade as a clean branch — which
    would let ``ensure_pr`` skip the checkpoint entirely.
    """
    out = _run_git(["diff", "--name-only", compare_ref, "HEAD"])
    return tuple(line.strip() for line in out.splitlines() if line.strip())


def _resolve_base_ref(base: str, remote: str) -> str:
    """Pick the ref ``base`` names in *this* checkout.

    The name returned by :func:`_default_base` (``"main"``) is not guaranteed
    to exist locally: a checkout may only carry the remote-tracking ref. A
    diff against an unknown ref fails, so resolve once and share the answer.
    The remote HEAD ref is the last resort, which makes the comparison
    independent of the base *name*.
    """
    for candidate in (f"{remote}/{base}", base, f"{remote}/HEAD"):
        try:
            _run_git(["rev-parse", "--verify", f"{candidate}^{{commit}}"])
        except RuntimeError:
            continue
        return candidate
    raise RuntimeError(f"cannot resolve base {base!r} to a git ref (tried "
                       f"{remote}/{base}, {base}, {remote}/HEAD)")


def _unpushed_commit_paths(compare_ref: str, branch: str) -> tuple[str, ...]:
    """Paths touched by commits the remote does not have yet.

    ``compare_ref`` must already be a resolvable ref (see
    :func:`_resolve_base_ref`). Raises when neither comparison can run — an
    uninspectable delta is not "nothing unpushed" (issue #1084), and reading
    it as such let a workflow path slip into the push that GitHub rejects.
    """
    try:
        out = _run_git(["diff", "--name-only", f"{branch}@{{u}}..HEAD"])
    except RuntimeError:
        out = _run_git([
            "diff", "--name-only", "--relative", "--no-renames", compare_ref, "HEAD"
        ])
    return tuple(line.strip() for line in out.splitlines() if line.strip())


def _reset_to_merge_base(publish_base: str) -> str:
    """Move HEAD back to the merge base of ``publish_base`` (soft).

    ``--mixed`` keeps the work in the working tree, so the subsequent
    ``_commit_pending_changes`` re-commits it without the workflow files.
    """
    base = _run_git(["merge-base", "HEAD", publish_base])
    _run_git(["reset", "--mixed", base])
    return base


def _prepare_publish(compare_ref: str, branch: str, publish_base: str) -> PublishSplit:
    """Partition the delta so no workflow path can reach the push.

    Dirty paths split directly. When the branch already has *committed*,
    unpushed workflow changes (the agent committed them before its own push
    was rejected) the publish commit is rebuilt from the merge base so those
    files stay out of the pushed history too.
    """
    dirty = _dirty_paths()
    workflow_dirty, plain_dirty = split_paths(dirty)

    republished = False
    committed = _unpushed_commit_paths(compare_ref, branch)
    # Unpushed commits may include workflow files staged *before* this call
    # (they are no longer in ``git status``). Rebuilding the publish commit
    # picks them up as dirty paths, so the delivery below still sees them.
    committed_workflow = [p for p in committed if is_workflow_path(p)]
    if committed_workflow:
        try:
            _reset_to_merge_base(publish_base)
            _unstage_workflow_paths(committed_workflow)
            republished = True
        except RuntimeError as exc:
            # Keep the workflow files out of the push regardless; the old
            # history is force-replaced by the push below.
            logger.warning(f"ensure_pr: could not rebuild the publish commit: {exc}")
            workflow_dirty = tuple(
                dict.fromkeys((*workflow_dirty, *committed_workflow))
            )
            return PublishSplit(
                workflow_paths=workflow_dirty,
                plain_paths=plain_dirty,
                republished=False,
            )
        workflow_dirty, plain_dirty = split_paths(_dirty_paths())

    return PublishSplit(
        workflow_paths=workflow_dirty,
        plain_paths=plain_dirty,
        republished=republished,
    )


def _push_branch(branch: str, remote: str, *, force: bool = False) -> None:
    args = ["push", remote, branch]
    try:
        upstream = _run_git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"])
    except RuntimeError:
        upstream = ""
    if not upstream:
        args += ["--set-upstream"]
    if force and upstream:
        # Only used after the publish commit was rebuilt from the merge base:
        # the commits that carried the workflow files must not survive on the
        # remote, or the next fetch would put them back in the way (issue #1080).
        args.append("--force-with-lease")
    _run_git(args)


def _find_pr_for_branch(
    branch: str, *, transport: Optional[GitHubTransport] = None
) -> Optional[str]:
    """Return the URL of an open PR for the branch, or None."""
    out = _run_gh(
        ["pr", "list", "--head", branch, "--json", "url", "--jq", ".[0].url"],
        transport=transport,
    )
    url = out.strip().strip('"')
    return url or None


@dataclass(frozen=True)
class PublicationState:
    """Immutable result of :func:`check_publication`.

    Fields:
        published: True when the branch has no unpublished work left.
        reasons: Why the work counts as unpublished — a subset of
            ``{"dirty_tree", "unpushed_commits", "missing_pr",
            "workflow_patch"}``.
        dirty_paths: The publishing dirtiness — every dirty path that is not
            engine-owned. Unpushed-commit-only paths are not included (they
            are not in ``git status``), and neither are workflow paths (those
            are patch-only, see ``workflow_paths``).
        workflow_paths: ``.github/workflows/*`` paths with unpublished
            changes — dirty *or* inside unpushed commits. These can never be
            pushed with the agent token, so the guard must not recall the
            agent for them (issue #1080).
        skip_reason: Set when the check could not run at all
            (``"no git remote"`` / ``"detached HEAD"``); ``published`` is True
            in that case and nothing is changed.
    """

    published: bool
    reasons: tuple[str, ...] = ()  # @stub-protected
    dirty_paths: tuple[str, ...] = ()
    # @stub-protected: signature defined by architect — do not modify
    workflow_paths: tuple[str, ...] = ()
    skip_reason: str = ""


def _dirty_paths() -> tuple[str, ...]:
    """Working-tree paths git reports, minus the engine-owned ones."""
    paths = []
    for path, _code in _status_entries():
        if not path or path in ENGINE_OWNED_PATHS:
            continue
        paths.append(path)
    return tuple(paths)


def _branch_holds_unpublished_commits(branch: str, remote: str) -> bool:
    """Whether HEAD has commits the remote does not have.

    With an upstream this is exactly ``@{u}..HEAD``. A branch that was never
    pushed has no upstream, so the comparison falls back to the repository's
    base branch — otherwise every fresh branch would look "unpushed" even
    when it holds nothing. The base is resolved to a real ref first, and a
    comparison that still fails raises rather than reading as "nothing
    unpushed" (issue #1084).
    """
    try:
        ahead = _run_git(["rev-list", "--count", "@{u}..HEAD"])
        return int(ahead or 0) > 0
    except RuntimeError:
        pass
    base_ref = _resolve_base_ref(_default_base(), remote)
    ahead = _run_git(["rev-list", "--count", f"{base_ref}..HEAD"])
    return int(ahead or 0) > 0


def check_publication() -> PublicationState:
    # @stub-protected: signature defined by architect — do not modify
    """Detect work that was never committed, pushed or opened as a PR.

    The publication guard (see ``run_agent.publication_guard``) calls this
    after an approved write step: the engine must not let a step end with the
    work sitting only in the checkout.

    Unpublished means any of:
      * the working tree holds changes (gitignored paths are invisible to
        ``git status``; engine-owned paths are excluded explicitly),
      * the current branch has commits the remote does not have,
      * the branch holds such commits but no open PR exists for it. (A branch
        with nothing on it is not "missing a PR" — opening an empty WIP PR
        would be noise.)

    ``.github/workflows/*`` changes are reported separately: they are
    unpublished (``workflow_patch``) but can never be pushed with the agent
    token, so the guard hands them to ``ensure_pr``'s patch delivery instead
    of recalling the agent for work it cannot publish (issue #1080).

    Never raises, mirroring :func:`ensure_pr`: any failure is logged and
    reported as a skip so the caller degrades gracefully.
    """
    try:
        remote = _default_remote()
        if remote is None:
            logger.info("check_publication: no git remote configured — skipping")
            return PublicationState(published=True, skip_reason="no git remote")

        branch = _current_branch()
        if branch in ("HEAD", ""):
            logger.info("check_publication: detached HEAD — skipping")
            return PublicationState(published=True, skip_reason="detached HEAD")

        dirty = _dirty_paths()
        workflow_dirty, plain_dirty = split_paths(dirty)
        reasons: list[str] = []
        if plain_dirty:
            reasons.append("dirty_tree")

        workflow_committed: tuple[str, ...] = ()
        try:
            has_branch_work = _branch_holds_unpublished_commits(branch, remote)
            if has_branch_work:
                # Workflow files that only live in unpushed commits are
                # patch-only too — the push carrying them would be rejected.
                base_ref = _resolve_base_ref(_default_base(), remote)
                committed = _unpushed_commit_paths(base_ref, branch)
                workflow_committed, plain_committed = split_paths(committed)
                if plain_committed:
                    reasons.append("unpushed_commits")
                if plain_committed or workflow_committed:
                    try:
                        pr_url = _find_pr_for_branch(branch)
                    except (RuntimeError, TimeoutExpired) as exc:
                        # A failing PR lookup must not become a publication
                        # failure — ensure_pr would fail the same way; surface
                        # it as a skip.
                        logger.warning(f"check_publication: PR lookup failed: {exc}")
                        return PublicationState(
                            published=True, skip_reason="pr lookup failed"
                        )
                    if plain_committed and not pr_url:
                        reasons.append("missing_pr")
        except RuntimeError as exc:
            # The git state cannot be inspected (e.g. no resolvable base ref).
            # Reading that as "nothing unpushed" is what let a workflow path
            # reach the rejected push in #1058 — report unpublished instead.
            logger.warning(f"check_publication: cannot inspect unpushed work: {exc}")
            return PublicationState(
                published=False,
                reasons=("unpushed_commits",),
                dirty_paths=dirty,
                workflow_paths=workflow_dirty,
            )

        workflow_paths = tuple(
            dict.fromkeys((*workflow_dirty, *workflow_committed))
        )
        if workflow_paths:
            reasons.append("workflow_patch")

        return PublicationState(
            published=not reasons,
            reasons=tuple(reasons),
            dirty_paths=dirty,
            workflow_paths=workflow_paths,
        )

    except Exception as exc:  # noqa: BLE001 - must never break the run
        logger.warning(f"check_publication failed: {exc}")
        return PublicationState(published=True, skip_reason=f"error: {exc}")


def _open_pr(
    repo: str,
    head: str,
    base: str,
    title: str,
    issue_number: Optional[int],
    *,
    transport: Optional[GitHubTransport] = None,
) -> str:
    body_lines = [
        "_This PR was opened automatically by the ensure_pr fallback because the "
        "agent reached its soft time budget (issue #489). It contains the "
        "checkpointed work from that run; a follow-up run will pick up from here._",
    ]
    if issue_number:
        body_lines.append("")
        body_lines.append(f"Closes #{issue_number}")
    args = [
        "pr",
        "create",
        "--repo",
        repo,
        "--head",
        head,
        "--base",
        base,
        "--title",
        title,
        "--body",
        "\n".join(body_lines),
    ]
    # The issue is linked via the "Closes #N" reference in the body above
    # (gh pr create has no dedicated --issue flag).
    url = _run_gh(args, mutative=True, transport=transport).strip()
    if not url:
        # A dropped content-creation write can exit 0 with an empty body; treat
        # it as a failure so the PR is never silently lost (issue #805).
        raise RuntimeError("gh pr create returned no PR URL (write may have been dropped)")
    return url


def _issue_number_from_env() -> Optional[int]:
    """TARGET_NUMBER is the triggering issue number, or 0 when absent."""
    raw = os.environ.get("TARGET_NUMBER", "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        return None
    return value if value > 0 else None


def _default_base(*, transport: Optional[GitHubTransport] = None) -> str:
    """Resolve the PR base branch for the current repository.

    GITHUB_BASE_REF is only set on pull_request triggers; on the
    issues/issue_comment/workflow_call triggers this repo uses it is empty,
    so the repository's default branch must be queried explicitly. ``gh``
    resolves the target repo from the ambient git context (remote origin /
    GITHUB_REPOSITORY), so no URL parsing is needed.
    """
    base = os.environ.get("GITHUB_BASE_REF", "").strip()
    if base:
        return base
    try:
        out = _run_gh(
            ["api", "repos/{owner}/{repo}", "--jq", ".default_branch"],
            transport=transport,
        ).strip().strip('"')
        if out:
            return out
    except (RuntimeError, TimeoutExpired):
        # A failing/timing-out network query must degrade to a logged
        # fallback ("main"), never escalate into the whole ensure_pr result.
        logger.warning("_default_base: gh api failed, using 'main'")
    return "main"


@dataclass(frozen=True)
class _PatchDelivery:
    """Where the workflow patch ended up (``comment_url``/``artifact`` empty
    when undelivered; ``diff`` is the patch text itself)."""

    comment_url: str = ""
    artifact: str = ""
    diff: str = ""


def _post_patch_comment(
    body: str,
    *,
    pr_url: str,
    transport: Optional[GitHubTransport] = None,
) -> str:
    """Post the patch comment on the PR, else on the originating issue.

    Returns the comment URL, or ``""`` when there is no target (a
    workflow-only change with no PR and no ``TARGET_NUMBER``) or GitHub
    refuses the write. The caller must then keep the tree dirty rather than
    discard the only copy of the work.
    """
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        logger.warning("ensure_pr: GITHUB_REPOSITORY unset — cannot post the patch")
        return ""
    target: Optional[int] = None
    if pr_url:
        tail = pr_url.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            target = int(tail)
    if target is None:
        target = _issue_number_from_env()
    if target is None:
        logger.warning(
            "ensure_pr: no PR and no TARGET_NUMBER — patch left as an artifact only"
        )
        return ""
    out = _run_gh(
        [
            "api",
            f"/repos/{repo}/issues/{target}/comments",
            "-f",
            f"body={ensure_bot_marker(body)}",
            "--jq",
            ".html_url",
        ],
        mutative=True,
        transport=transport,
    )
    return out.strip().strip('"')


def _render_patch(workflow_paths: Sequence[str], patch_base: str, base_name: str):
    """Build the diff and render its comment body."""
    diff = build_patch(
        base_ref=patch_base, workflow_paths=workflow_paths, run_git=_run_git
    )
    if not diff.strip():
        # Delivery alias: identical content on both sides, nothing to apply.
        return None
    return diff, render_patch_comment(
        diff=diff, workflow_paths=workflow_paths, base_name=base_name
    )


def _deliver_workflow_patch(
    *,
    patch_base: str,
    base_name: str,
    workflow_paths: Sequence[str],
    transport: Optional[GitHubTransport] = None,
) -> _PatchDelivery:
    """Build, comment and stage the workflow patch.

    Raises on any failure: an undelivered patch must abort the flow before the
    workflow files are discarded (issue #1080 — silent loss is the one outcome
    that is not allowed).
    """
    rendered = _render_patch(workflow_paths, patch_base, base_name)
    if rendered is None:
        return _PatchDelivery()
    diff, comment = rendered
    comment_url = _post_patch_comment(
        comment.body, pr_url="", transport=transport
    )
    artifact = stage_patch_artifact(diff)
    return _PatchDelivery(comment_url=comment_url, artifact=artifact, diff=diff)


def _stage_patch_commit_file(diff: str) -> str:
    """Write the patch into the checkout and stage it for the push commit.

    The patch is delivered as a comment/artifact for a human to apply, but the
    work must also survive in the repository itself (the runner is ephemeral):
    the diff file is committed under ``nebuko-task-logs/`` and pushed with the
    branch. Returns the repo-relative path, or ``""`` when staging failed —
    the comment/artifact delivery above already carries the content.
    """
    relative = os.path.join(PATCH_ARTIFACT_DIR, PATCH_FILENAME)
    try:
        root = _run_git(["rev-parse", "--show-toplevel"])
        if not os.path.isdir(root):
            # Mocked git seam (tests): there is no real checkout to stage into.
            return ""
        os.makedirs(os.path.join(root, PATCH_ARTIFACT_DIR), exist_ok=True)
        with open(
            os.path.join(root, relative), "w", encoding="utf-8"
        ) as handle:
            handle.write(diff if diff.endswith("\n") else diff + "\n")
        _run_git(["add", "--", relative])
        return relative
    except (OSError, RuntimeError) as exc:
        logger.warning(f"ensure_pr: could not stage the patch file: {exc}")
        return ""


def _ensure_pr_impl(dry_run: bool) -> EnsurePrResult:
    """The real ``ensure_pr`` body (wrapped by the public entry point)."""
    # One transport for the whole flow so the PR lookup, the PR creation and
    # the patch comment share a single pacing/budget state (issue #805).
    transport = GitHubTransport()
    remote = _default_remote()
    if remote is None:
        logger.info("ensure_pr: no git remote configured — skipping")
        return EnsurePrResult(skipped=True, message="no git remote")

    branch = _current_branch()
    if branch in ("HEAD", ""):
        logger.info("ensure_pr: detached HEAD — skipping")
        return EnsurePrResult(skipped=True, message="detached HEAD")

    # ``patch_base`` is the diff anchor (must be a resolvable ref: a checkout
    # may only carry ``origin/main``); ``compare_ref`` is the
    # branch-comparison anchor.
    base = _default_base(transport=transport)
    patch_base = _resolve_base_ref(base, remote)
    try:
        compare_ref = _run_git(["rev-parse", "--abbrev-ref", "@{u}"]).strip() or patch_base
    except RuntimeError:
        compare_ref = patch_base
    publish_base = patch_base

    split = _prepare_publish(compare_ref, branch, publish_base)

    if dry_run:
        logger.info(
            f"[DRY RUN] ensure_pr: would commit {list(split.plain_paths)} and push "
            f"'{branch}' to {remote}"
        )
        if split.workflow_paths:
            logger.info(
                "[DRY RUN] ensure_pr: would deliver a patch for "
                f"{list(split.workflow_paths)}"
            )
        return EnsurePrResult(
            commit_hash=_run_git(["rev-parse", "HEAD"]),
            message="[dry run] would open PR",
            patch_paths=split.workflow_paths,
        )

    delivery = _PatchDelivery()
    if split.workflow_paths:
        delivery = _deliver_workflow_patch(
            patch_base=patch_base,
            base_name=base,
            workflow_paths=split.workflow_paths,
            transport=transport,
        )
        if not delivery.comment_url and not delivery.artifact:
            raise RuntimeError(
                "workflow patch could not be delivered (no comment target?) — "
                "refusing to discard .github/workflows changes"
            )
        # The patch itself must survive the ephemeral runner: commit it on the
        # branch so it is pushed alongside the plain work (never the workflow
        # files themselves — those are reverted below).
        patch_commit_path = _stage_patch_commit_file(delivery.diff)
    else:
        patch_commit_path = ""

    commit_paths = list(split.plain_paths)
    if patch_commit_path:
        commit_paths.append(patch_commit_path)
    commit_hash = ""
    if _has_uncommitted_changes():
        commit_hash = _commit_pending_changes(commit_paths)
        logger.info(f"ensure_pr: committed pending changes at {commit_hash[:12]}")

    # Workflow-only: no plain delta means no push, no PR — the patch *is* the
    # publication. The patch file is still committed on the branch above (so it
    # survives the ephemeral runner); a mixed delta pushes it alongside the
    # plain work.
    no_plain_delta = not split.plain_paths and not split.republished

    pushed = False
    if not no_plain_delta:
        _push_branch(branch, remote, force=split.republished)
        pushed = True
        logger.info(f"ensure_pr: pushed branch '{branch}' to {remote}")

    commit_hash = commit_hash or _run_git(["rev-parse", "HEAD"])

    existing = _find_pr_for_branch(branch, transport=transport)
    pr_url = existing or ""
    if not pr_url and not no_plain_delta:
        repo = _run_git(["remote", "get-url", remote])
        issue_number = _issue_number_from_env()
        title = (
            "WIP checkpoint: in-flight work from interrupted agent run "
            f"(#{issue_number or 'n/a'})"
        )
        pr_url = _open_pr(repo, branch, base, title, issue_number, transport=transport)
        logger.info(f"ensure_pr: opened PR {pr_url}")
    elif existing:
        logger.info(f"ensure_pr: PR already exists for '{branch}': {existing}")

    if split.workflow_paths:
        # Posted before the PR existed, so re-target the patch comment at the
        # PR when one was opened; the issue comment already carries it, so a
        # failed re-post is logged and otherwise ignored.
        if delivery.comment_url and pr_url and not delivery.comment_url.startswith(
            pr_url
        ):
            try:
                rendered = _render_patch(split.workflow_paths, patch_base, base)
                comment_url = _post_patch_comment(
                    rendered[1].body if rendered else "",
                    pr_url=pr_url,
                    transport=transport,
                )
                if comment_url:
                    delivery = _PatchDelivery(
                        comment_url=comment_url, artifact=delivery.artifact
                    )
            except (RuntimeError, TimeoutExpired) as exc:
                logger.warning(f"ensure_pr: PR patch re-post failed: {exc}")
        revert_workflow_changes(
            base_ref=patch_base, workflow_paths=split.workflow_paths, run_git=_run_git
        )
        logger.info(
            "ensure_pr: delivered the workflow patch for "
            f"{list(split.workflow_paths)} (comment={delivery.comment_url or 'none'}, "
            f"artifact={delivery.artifact or 'none'})"
        )


    message = "PR opened: " + pr_url if pr_url else "workflow patch delivered"
    return EnsurePrResult(
        commit_hash=commit_hash,
        pushed=pushed,
        pr_url=pr_url,
        message=message,
        patch_comment_url=delivery.comment_url,
        patch_paths=split.workflow_paths,
        patch_artifact=delivery.artifact,
    )


def ensure_pr(dry_run: bool = False) -> EnsurePrResult:
    """Commit/push/open a PR for the current branch — idempotent.

    Never raises: any failure is logged and reported in the returned
    :class:`EnsurePrResult` so the caller (build_output) degrades gracefully.

    ``.github/workflows/*`` paths are never staged, committed or pushed: the
    agent token has no ``workflow`` scope, so GitHub rejects such a push
    outright and the fallback used to fail with it (issue #1080). Those paths
    are delivered as a ``git apply``-able patch instead — as a comment on the
    PR, or on the originating issue when the change is workflow-only.
    ``GITHUB_REPOSITORY`` missing makes the patch undeliverable and is
    reported as an error rather than losing the work.

    Args:
        dry_run: If True, log intended actions without mutating git state.

    Returns:
        EnsurePrResult describing what happened.
    """
    try:
        return _ensure_pr_impl(dry_run)
    except Exception as e:
        logger.error(f"ensure_pr failed: {e}")
        return EnsurePrResult(error=True, message=f"error: {e}")

