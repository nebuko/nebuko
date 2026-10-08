#!/usr/bin/env python3
"""
Shared GitHub API utilities for workflow scripts.

Provides subprocess-based wrappers around the GitHub CLI (gh) for:
- Fetching issue comments
- Posting comments
- Getting comment node IDs
- Minimizing comments

Every call is funnelled through :class:`GitHubTransport`, the single choke
point that paces mutative requests, classifies rate-limit / transient
failures and retries with bounded exponential backoff (issue #805). Writes
return typed results (:class:`PostCommentResult`) so a dropped comment can
never be mistaken for a success.

Supports --dry-run mode for testing without making actual API calls.

Usage:
    from github_client import get_issue_comments, post_comment
"""

import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# Universal bot marker prepended to every CI-posted comment so that
# human-vs-bot classification is deterministic (see is_bot_comment).
BOT_MARKER = "<!-- bot-workflow-terminal -->"

# GitHub's own guidance is to pause at least one second between mutative
# requests to avoid tripping the secondary rate limit.
DEFAULT_MIN_WRITE_GAP_S = 1.0
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_BASE_S = 2.0
DEFAULT_BACKOFF_CAP_S = 60.0
# Secondary content-creation blocks often arrive without a ``Retry-After``
# header, so fall back to GitHub's documented "wait at least one minute".
DEFAULT_SECONDARY_COOLDOWN_S = 60.0
LOW_REMAINING_THRESHOLD = 50
# GitHub caps every REST page at 100 items; a bounded tail read uses one
# discovery page plus the last page(s) needed to cover ``limit`` comments.
COMMENTS_PER_PAGE = 100

_STATUS_RE = re.compile(r"HTTP[ /](\d{3})")
_LINK_LAST_RE = re.compile(r'<[^>]*[?&]page=(\d+)[^>]*>\s*;\s*rel="last"')


class ApiErrorKind(Enum):
    """Classification of a failed GitHub API call."""

    PRIMARY_RATE_LIMIT = "primary_rate_limit"
    SECONDARY_RATE_LIMIT = "secondary_rate_limit"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    TRANSIENT = "transient"
    DECODE = "decode"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ApiCallResult:
    """Typed outcome of one ``gh`` invocation through the transport."""

    ok: bool
    status: Optional[int]
    payload: str
    error_kind: Optional[ApiErrorKind]
    detail: str
    # Response headers when ``gh api --include`` surfaced them (pagination
    # ``Link``, rate-limit counters). Empty for calls without header capture.
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PostCommentResult:
    """Typed outcome of a comment write (never ``None`` on failure)."""

    ok: bool
    comment_url: str = ""
    comment_id: Optional[int] = None
    error_kind: Optional[ApiErrorKind] = None
    detail: str = ""


@dataclass(frozen=True)
class CommentsFetchResult:
    """Typed outcome of a comment-thread read.

    ``error_kind`` distinguishes a genuine fetch failure from a legitimately
    empty thread, so a failed state fetch can never masquerade as "no state".
    """

    ok: bool
    comments: Tuple[dict, ...] = ()
    error_kind: Optional[ApiErrorKind] = None
    detail: str = ""


# Kinds worth retrying: rate limits and server/network transients. Auth,
# not-found and decode failures are deterministic and fail fast.
_RETRYABLE_KINDS = frozenset(
    {
        ApiErrorKind.PRIMARY_RATE_LIMIT,
        ApiErrorKind.SECONDARY_RATE_LIMIT,
        ApiErrorKind.TRANSIENT,
    }
)


def ensure_bot_marker(body: str) -> str:
    """Return body with the universal bot marker guaranteed on line 1.

    The marker is injected idempotently: if the body (ignoring leading
    whitespace) already starts with BOT_MARKER it is returned unchanged,
    otherwise the marker is prepended as its own leading line. The rest of
    the body is unchanged.

    Args:
        body: Raw comment body text.

    Returns:
        Comment body whose first line is BOT_MARKER (never duplicated).
    """
    if body.lstrip().startswith(BOT_MARKER):
        return body
    return BOT_MARKER + "\n" + body


def _gh_cli() -> List[str]:
    """Return gh CLI path."""
    return ["gh"]


def _run(
    args: List[str],
    dry_run: bool = False,
    input_data: Optional[str] = None,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    """Run a gh CLI command and return the result."""
    if dry_run:
        print(f"[DRY RUN] Would run: gh {' '.join(args)}", file=sys.stderr)
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")
    return subprocess.run(
        _gh_cli() + args,
        capture_output=True,
        text=True,
        input=input_data,
        env=env,
        cwd=cwd,
        timeout=timeout,
    )


def _resolve_token(token: Optional[str]) -> Optional[str]:
    """Resolve GitHub token from explicit param or environment variables.

    Priority: explicit token > GH_TOKEN env var > GITHUB_TOKEN env var.
    """
    if token:
        return token
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _flatten_pages(parsed):
    """Flatten the outer page array produced by ``gh api --paginate --slurp``.

    ``--slurp`` wraps each page in an outer array (``[[page1], [page2]]``);
    a flat list (a single-page/unpaginated response) is returned unchanged.
    """
    if not isinstance(parsed, list):
        return []
    if parsed and isinstance(parsed[0], list):
        return [comment for page in parsed for comment in page]
    return parsed


def _parse_status_line(line: str) -> Optional[int]:
    """Extract the numeric status code from an ``HTTP/1.1 200 OK`` line."""
    parts = line.split()
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return None


def _split_include(stdout: str) -> Tuple[Optional[int], Dict[str, str], str]:
    """Split ``gh api --include`` output into (status, headers, body).

    Non-``--include`` output (no leading ``HTTP/`` line) is returned unchanged
    with ``status=None`` and no headers.
    """
    text = stdout.replace("\r\n", "\n")
    if not text.startswith("HTTP/"):
        return None, {}, stdout
    head, _, body = text.partition("\n\n")
    lines = head.split("\n")
    status = _parse_status_line(lines[0])
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return status, headers, body


def _status_from_text(text: str) -> Optional[int]:
    """Best-effort extraction of an HTTP status from ``gh`` error output."""
    match = _STATUS_RE.search(text or "")
    return int(match.group(1)) if match else None


# @stub-protected
class GitHubTransport:
    """Single choke point for every GitHub API call.

    Owns the write-pacing clock, rate-limit classification and the bounded
    exponential backoff used by every ``gh`` invocation in this module. The
    mutable budget fields (``remaining``/``reset_at``/``cooldown_until``) are
    observed from the response headers of each call and read back by
    :meth:`_pace`.

    Args:
        token: Explicit token; falls back to ``GH_TOKEN``/``GITHUB_TOKEN``.
        min_write_gap_s: Minimum spacing enforced between mutative calls.
        max_attempts: Total attempts per call (1 disables retries).
        backoff_base_s: Exponential backoff base for transient failures.
        backoff_cap_s: Upper bound on any single backoff sleep.
        secondary_cooldown_s: Fallback wait for a secondary block without
            ``Retry-After``.
        low_remaining_threshold: Pre-emptively wait when the observed primary
            budget drops below this and the window resets soon.
        sleep: Injectable sleeper (tests pass a no-op).
        monotonic: Injectable monotonic clock.
        wall_clock: Injectable epoch clock (rate-limit reset timestamps).
        jitter: Injectable jitter source returning ``[0, 1)`` seconds.
    """

    # @stub-protected
    def __init__(
        self,
        token: Optional[str] = None,
        *,
        min_write_gap_s: float = DEFAULT_MIN_WRITE_GAP_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        backoff_cap_s: float = DEFAULT_BACKOFF_CAP_S,
        secondary_cooldown_s: float = DEFAULT_SECONDARY_COOLDOWN_S,
        low_remaining_threshold: int = LOW_REMAINING_THRESHOLD,
        sleep: Optional[Callable[[float], None]] = None,
        monotonic: Optional[Callable[[], float]] = None,
        wall_clock: Optional[Callable[[], float]] = None,
        jitter: Optional[Callable[[], float]] = None,
    ) -> None:
        self.token = _resolve_token(token)
        self.min_write_gap_s = float(min_write_gap_s)
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_base_s = float(backoff_base_s)
        self.backoff_cap_s = float(backoff_cap_s)
        self.secondary_cooldown_s = float(secondary_cooldown_s)
        self.low_remaining_threshold = int(low_remaining_threshold)
        self._sleep = sleep if sleep is not None else time.sleep
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._wall_clock = wall_clock if wall_clock is not None else time.time
        self._jitter = jitter if jitter is not None else (lambda: random.uniform(0.0, 1.0))
        self.remaining: Optional[int] = None
        self.reset_at: Optional[float] = None
        self.cooldown_until: Optional[float] = None
        self._last_write_at: Optional[float] = None
        self.calls = 0
        self.retries = 0

    # @stub-protected
    def call(
        self,
        args: Sequence[str],
        *,
        mutative: bool,
        env: Optional[Mapping[str, str]] = None,
        input_data: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ApiCallResult:
        """Run one ``gh`` invocation with pacing and bounded retries."""
        last = ApiCallResult(
            ok=False,
            status=None,
            payload="",
            error_kind=ApiErrorKind.UNKNOWN,
            detail="no attempt made",
        )
        for attempt in range(1, self.max_attempts + 1):
            self._pace(mutative=mutative)
            if mutative:
                self._last_write_at = self._monotonic()
            self.calls += 1
            completed = self._exec(
                args, env=env, input_data=input_data, cwd=cwd, timeout=timeout
            )
            result, headers = self._interpret(completed)
            self._observe(headers)
            kind = self._classify(result, headers)
            result = replace(result, error_kind=kind, headers=dict(headers))
            last = result
            if result.ok or kind not in _RETRYABLE_KINDS or attempt >= self.max_attempts:
                return result
            self.retries += 1
            self._backoff(kind, attempt)
        return last

    # @stub-protected
    def _pace(self, *, mutative: bool) -> None:
        """Honour an active cooldown, then space mutative requests apart."""
        self._throttle_if_low()
        now = self._monotonic()
        if self.cooldown_until is not None:
            wait = self.cooldown_until - now
            if wait > 0:
                self._sleep(wait)
            self.cooldown_until = None
        if mutative and self._last_write_at is not None:
            gap = self.min_write_gap_s - (self._monotonic() - self._last_write_at)
            if gap > 0:
                self._sleep(gap)

    def _throttle_if_low(self) -> None:
        """Wait out an imminent primary-budget reset when almost exhausted.

        Bounded by ``backoff_cap_s``: a reset further away than one backoff
        window is not worth stalling for, and the per-call retry loop handles
        any hard 403 that still lands.
        """
        if self.remaining is None or self.remaining >= self.low_remaining_threshold:
            return
        if self.reset_at is None:
            return
        wait = self.reset_at - self._wall_clock()
        if 0 < wait <= self.backoff_cap_s:
            self._sleep(wait)
            self.remaining = self.low_remaining_threshold

    # @stub-protected
    def _classify(
        self, result: ApiCallResult, headers: Optional[Mapping[str, str]] = None
    ) -> Optional[ApiErrorKind]:
        """Classify a failed call into an :class:`ApiErrorKind`."""
        if result.ok:
            return None
        headers = headers or {}
        text = f"{result.detail} {result.payload}".lower()
        status = result.status
        if status == 404 or "http 404" in text or "not found" in text:
            return ApiErrorKind.NOT_FOUND
        if status == 401 or "bad credentials" in text or "http 401" in text:
            return ApiErrorKind.AUTH
        if "secondary rate limit" in text:
            return ApiErrorKind.SECONDARY_RATE_LIMIT
        if status in (403, 429) or "http 403" in text or "http 429" in text:
            if headers.get("x-ratelimit-remaining") == "0":
                return ApiErrorKind.PRIMARY_RATE_LIMIT
            if "rate limit exceeded" in text:
                return ApiErrorKind.PRIMARY_RATE_LIMIT
            return ApiErrorKind.SECONDARY_RATE_LIMIT
        if "unexpected end of json input" in text or "failed to parse" in text:
            return ApiErrorKind.DECODE
        if status is not None and 500 <= status < 600:
            return ApiErrorKind.TRANSIENT
        if status is None and not text:
            return ApiErrorKind.TRANSIENT
        return ApiErrorKind.UNKNOWN

    # @stub-protected
    def _observe(self, headers: Mapping[str, str]) -> None:
        """Track the primary budget and any server-mandated cooldown."""
        if not headers:
            return
        remaining = headers.get("x-ratelimit-remaining")
        if remaining is not None:
            try:
                self.remaining = int(remaining)
            except ValueError:
                pass
        reset = headers.get("x-ratelimit-reset")
        if reset is not None:
            try:
                self.reset_at = float(reset)
            except ValueError:
                pass
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                self.cooldown_until = self._monotonic() + max(0.0, float(retry_after))
            except ValueError:
                pass

    def _backoff(self, kind: Optional[ApiErrorKind], attempt: int) -> None:
        # Cap only the exponential component: a server-mandated Retry-After or
        # rate-limit reset must be honoured in full.
        delay = min(self.backoff_base_s * (2 ** (attempt - 1)), self.backoff_cap_s)
        if kind == ApiErrorKind.SECONDARY_RATE_LIMIT:
            if self.cooldown_until is not None:
                delay = max(delay, max(0.0, self.cooldown_until - self._monotonic()))
                self.cooldown_until = None
            else:
                delay = max(delay, self.secondary_cooldown_s)
        elif kind == ApiErrorKind.PRIMARY_RATE_LIMIT and self.reset_at is not None:
            delay = max(delay, max(0.0, self.reset_at - self._wall_clock()))
        self._sleep(delay + self._jitter())

    def _env(self, env: Optional[Mapping[str, str]]) -> Dict[str, str]:
        merged = dict(os.environ)
        if env:
            merged.update(env)
        if self.token:
            merged["GH_TOKEN"] = self.token
        return merged

    def _exec(
        self,
        args: Sequence[str],
        env: Optional[Mapping[str, str]] = None,
        input_data: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess:
        argv = list(args)
        if argv and argv[0] == "api" and "--paginate" not in argv and "--jq" not in argv:
            # Capture response headers for budget observation. Skipped for
            # paginated (headers repeat per page) and ``--jq`` (raw) output.
            argv = ["api", "--include", *argv[1:]]
        return _run(
            argv, env=self._env(env), input_data=input_data, cwd=cwd, timeout=timeout
        )

    @staticmethod
    def _interpret(completed: subprocess.CompletedProcess) -> Tuple[ApiCallResult, Dict[str, str]]:
        stdout = completed.stdout or ""
        stderr = (completed.stderr or "").strip()
        status, headers, body = _split_include(stdout)
        payload = body if status is not None else stdout
        if status is None and completed.returncode != 0:
            status = _status_from_text(stderr) or _status_from_text(payload)
        ok = completed.returncode == 0
        detail = stderr or ("" if ok else payload.strip())
        result = ApiCallResult(
            ok=ok, status=status, payload=payload, error_kind=None, detail=detail
        )
        return result, headers


def _default_transport(token: Optional[str] = None, **kwargs) -> GitHubTransport:
    """Build the transport a module-level function uses when none is injected."""
    return GitHubTransport(token, **kwargs)


def _last_page_from_link(link: Optional[str]) -> Optional[int]:
    """Parse the ``rel="last"`` page number out of a GitHub ``Link`` header."""
    if not link:
        return None
    match = _LINK_LAST_RE.search(link)
    return int(match.group(1)) if match else None


def _decode_comments_page(
    result: ApiCallResult,
) -> Tuple[Optional[List[dict]], Optional[CommentsFetchResult]]:
    """Decode one comment page into ``(comments, error_result)``."""
    try:
        parsed = json.loads(result.payload) if result.payload.strip() else []
    except json.JSONDecodeError as e:
        return None, CommentsFetchResult(
            ok=False, error_kind=ApiErrorKind.DECODE, detail=str(e)
        )
    # ``_flatten_pages`` tolerates both the flat single-page array and the
    # ``--slurp`` nested shape, so callers stay agnostic to the fetch mode.
    return _flatten_pages(parsed), None


def _read_comments_page(
    repo: str,
    issue_number: int,
    page: int,
    transport: GitHubTransport,
) -> Tuple[Optional[List[dict]], Mapping[str, str], Optional[CommentsFetchResult]]:
    """Fetch one 100-comment page; return ``(comments, headers, error_result)``."""
    path = f"/repos/{repo}/issues/{issue_number}/comments?per_page={COMMENTS_PER_PAGE}"
    if page > 1:
        path += f"&page={page}"
    result = transport.call(["api", path], mutative=False)
    if not result.ok:
        print(f"Error fetching comments: {result.detail}", file=sys.stderr)
        return None, result.headers, CommentsFetchResult(
            ok=False, error_kind=result.error_kind, detail=result.detail
        )
    comments, error = _decode_comments_page(result)
    return comments, result.headers, error


def _fetch_all_comments(
    repo: str,
    issue_number: int,
    transport: GitHubTransport,
) -> CommentsFetchResult:
    """Read the whole thread with ``--paginate --slurp`` (oldest first)."""
    result = transport.call(
        [
            "api",
            "--paginate",
            "--slurp",
            f"/repos/{repo}/issues/{issue_number}/comments",
        ],
        mutative=False,
    )

    if not result.ok:
        print(f"Error fetching comments: {result.detail}", file=sys.stderr)
        return CommentsFetchResult(
            ok=False, error_kind=result.error_kind, detail=result.detail
        )

    try:
        comments = _flatten_pages(json.loads(result.payload))
    except json.JSONDecodeError as e:
        print(f"Failed to parse comments JSON: {e}", file=sys.stderr)
        return CommentsFetchResult(ok=False, error_kind=ApiErrorKind.DECODE, detail=str(e))

    return CommentsFetchResult(ok=True, comments=tuple(comments))


def _fetch_comments_tail(
    repo: str,
    issue_number: int,
    limit: int,
    transport: GitHubTransport,
) -> CommentsFetchResult:
    """Fetch only the last ``limit`` comments instead of the whole thread.

    The first page reveals the last page number via the ``Link`` header, then
    the tail pages that cover ``limit`` are fetched directly — a bounded number
    of requests regardless of thread length (issue #805, Pillar C).
    """
    comments, headers, error = _read_comments_page(repo, issue_number, 1, transport)
    if error is not None:
        return error
    tail: List[dict] = list(comments or [])

    last_page = _last_page_from_link(headers.get("link"))
    if last_page is None and len(tail) >= COMMENTS_PER_PAGE:
        # The thread spans more than one page but the ``Link`` header did not
        # expose the last page, so the tail jump cannot be trusted. Fall back
        # to a full read rather than silently returning the *first* page's
        # comments as if they were the newest (issue #805 review).
        full = _fetch_all_comments(repo, issue_number, transport)
        if not full.ok:
            return full
        tail = list(full.comments)
    elif last_page is not None and last_page > 1:
        pages_needed = max(1, (limit + COMMENTS_PER_PAGE - 1) // COMMENTS_PER_PAGE)
        start = max(1, last_page - pages_needed + 1)
        tail = []
        for page in range(start, last_page + 1):
            page_comments, _, page_error = _read_comments_page(
                repo, issue_number, page, transport
            )
            if page_error is not None:
                return page_error
            tail.extend(page_comments or [])

    if limit > 0:
        tail = tail[-limit:]
    else:
        tail = []
    return CommentsFetchResult(ok=True, comments=tuple(tail))


def get_issue_comments(
    repo: str,
    issue_number: int,
    token: Optional[str] = None,
    dry_run: bool = False,
    *,
    limit: Optional[int] = None,
    transport: Optional[GitHubTransport] = None,
) -> List[dict]:
    """Fetch all comments from an issue via gh CLI.

    Paginates so the whole thread is returned in API order (oldest first).
    Without pagination only the first page (~30 comments) is fetched, which
    makes any ``comments[-N:]`` "last N" slice select the wrong end of the
    thread (issue #750). ``limit`` keeps only the last ``limit`` comments.

    Args:
        repo: Repository in format owner/repo.
        issue_number: Issue number.
        token: GitHub token (falls back to GH_TOKEN env var).
        dry_run: If True, return empty list without making API call.
        limit: Keep only the last ``limit`` comments (``None`` = all).
        transport: Injected transport (tests/unification).

    Returns:
        List of comment dicts with 'body', 'user', 'id', etc.
    """
    return list(
        fetch_issue_comments(
            repo,
            issue_number,
            token,
            dry_run=dry_run,
            limit=limit,
            transport=transport,
        ).comments
    )


def fetch_issue_comments(
    repo: str,
    issue_number: int,
    token: Optional[str] = None,
    dry_run: bool = False,
    *,
    limit: Optional[int] = None,
    transport: Optional[GitHubTransport] = None,
) -> CommentsFetchResult:
    """Typed variant of :func:`get_issue_comments`.

    Unlike the list-returning wrapper, the result carries ``ok``/``error_kind``
    so a failed fetch is distinguishable from an empty thread.
    """
    if dry_run:
        return CommentsFetchResult(ok=True)

    transport = transport or _default_transport(token)

    if limit is not None:
        # Bounded tail read: a constant number of requests for any thread
        # length, instead of paginating the entire history (issue #805).
        return _fetch_comments_tail(repo, issue_number, limit, transport)

    return _fetch_all_comments(repo, issue_number, transport)


def get_comment_body(
    comment_id: str,
    repo: str,
    token: Optional[str] = None,
    dry_run: bool = False,
    *,
    transport: Optional[GitHubTransport] = None,
) -> Optional[str]:
    """Fetch the body text of a GitHub issue comment by its numeric ID.

    Args:
        comment_id: The numeric comment ID.
        repo: Repository in format owner/repo.
        token: GitHub token.
        dry_run: If True, return None.
        transport: Injected transport (tests/unification).

    Returns:
        Comment body text, or None on failure.
    """
    if dry_run:
        return None

    transport = transport or _default_transport(token)
    result = transport.call(
        ["api", f"/repos/{repo}/issues/comments/{comment_id}", "--jq", ".body"],
        mutative=False,
    )
    if not result.ok:
        print(f"Error fetching comment body: {result.detail}", file=sys.stderr)
        return None

    body = result.payload.strip()
    return body if body else None


def get_comment_node_id(
    comment_id: str,
    repo: str,
    token: Optional[str] = None,
    dry_run: bool = False,
    *,
    transport: Optional[GitHubTransport] = None,
) -> Optional[str]:
    """Fetch GraphQL node_id for a given comment ID.

    Args:
        comment_id: The numeric or string comment ID.
        repo: Repository in format owner/repo.
        token: GitHub token.
        dry_run: If True, return None.
        transport: Injected transport (tests/unification).

    Returns:
        GraphQL node_id string, or None on failure.
    """
    if dry_run:
        return None

    transport = transport or _default_transport(token)
    result = transport.call(
        ["api", f"/repos/{repo}/issues/comments/{comment_id}", "--jq", ".node_id"],
        mutative=False,
    )
    if not result.ok:
        print(f"Error fetching node ID: {result.detail}", file=sys.stderr)
        return None

    node_id = result.payload.strip()
    return node_id if node_id else None


def post_comment(
    repo: str,
    issue_number: int,
    body: str,
    token: Optional[str] = None,
    dry_run: bool = False,
    *,
    transport: Optional[GitHubTransport] = None,
) -> PostCommentResult:
    """Post a comment to a GitHub issue.

    The universal bot marker (BOT_MARKER) is prepended to the body before
    posting so every CI-emitted comment is identifiable as bot-generated.

    Args:
        repo: Repository in format owner/repo.
        issue_number: Issue number.
        body: Comment body text.
        token: GitHub token.
        dry_run: If True, skip the API call.
        transport: Injected transport (tests/unification).

    Returns:
        A :class:`PostCommentResult`; ``ok`` is ``False`` (with an
        ``error_kind``) when the post could not be delivered. Never ``None``,
        so a dropped write can no longer be mistaken for a success.
    """
    body = ensure_bot_marker(body)

    if dry_run:
        print(f"[DRY RUN] Would post comment to {repo}#{issue_number}", file=sys.stderr)
        return PostCommentResult(ok=True, detail="dry-run")

    transport = transport or _default_transport(token)
    result = transport.call(
        ["api", f"/repos/{repo}/issues/{issue_number}/comments", "-f", f"body={body}"],
        mutative=True,
    )

    if not result.ok:
        print(f"Error posting comment: {result.detail}", file=sys.stderr)
        return PostCommentResult(ok=False, error_kind=result.error_kind, detail=result.detail)

    try:
        data = json.loads(result.payload) if result.payload.strip() else {}
    except json.JSONDecodeError as e:
        print(f"Failed to parse comment response: {e}", file=sys.stderr)
        return PostCommentResult(ok=False, error_kind=ApiErrorKind.DECODE, detail=str(e))

    comment_id = data.get("id")
    return PostCommentResult(
        ok=True,
        comment_url=str(data.get("html_url") or ""),
        comment_id=comment_id if isinstance(comment_id, int) else None,
    )


_MINIMIZE_QUERY = """
mutation($subjectId: ID!) {
  minimizeComment(input: {subjectId: $subjectId, classifier: OUTDATED}) {
    minimizedComment { minimizedReason }
  }
}
"""


def minimize_comment(
    node_id: str,
    token: Optional[str] = None,
    dry_run: bool = False,
    *,
    transport: Optional[GitHubTransport] = None,
) -> bool:
    """Minimize a comment via GraphQL mutation.

    Args:
        node_id: GraphQL node_id of the comment to minimize.
        token: GitHub token.
        dry_run: If True, skip the API call.
        transport: Injected transport (tests/unification).

    Returns:
        True if successful, False otherwise.
    """
    if dry_run:
        print(f"[DRY RUN] Would minimize comment {node_id}", file=sys.stderr)
        return True

    transport = transport or _default_transport(token)
    result = transport.call(
        ["api", "graphql", "-f", "query=" + _MINIMIZE_QUERY, "-f", f"subjectId={node_id}"],
        mutative=True,
    )
    if not result.ok:
        print(f"Error minimizing comment: {result.detail}", file=sys.stderr)
        return False

    return True


def delete_comment(
    repo: str,
    comment_id: int,
    token: Optional[str] = None,
    dry_run: bool = False,
    *,
    transport: Optional[GitHubTransport] = None,
) -> bool:
    """Delete a single issue comment via the REST API.

    An already-gone comment (HTTP 404) counts as success so a batch deletion
    stays idempotent.

    Args:
        repo: Repository in format owner/repo.
        comment_id: Numeric id of the comment to delete.
        token: GitHub token (falls back to GH_TOKEN env var).
        dry_run: If True, skip the API call.
        transport: Injected transport (tests/unification).

    Returns:
        True when the comment is gone (deleted or already absent), else False.
    """
    if dry_run:
        print(f"[DRY RUN] Would delete comment {comment_id}", file=sys.stderr)
        return True

    transport = transport or _default_transport(token)
    result = transport.call(
        ["api", "--method", "DELETE", f"/repos/{repo}/issues/comments/{comment_id}"],
        mutative=True,
    )
    if result.ok or result.error_kind == ApiErrorKind.NOT_FOUND:
        return True

    print(f"Error deleting comment {comment_id}: {result.detail}", file=sys.stderr)
    return False


def main():
    """CLI interface for testing individual functions."""
    import argparse

    parser = argparse.ArgumentParser(description="GitHub CLI client utilities")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # get-comments
    p_get = subparsers.add_parser("get-comments", help="Get issue comments")
    p_get.add_argument("--repo", required=True)
    p_get.add_argument("--issue-number", type=int, required=True)
    p_get.add_argument("--token")
    p_get.add_argument("--dry-run", action="store_true")

    # get-node-id
    p_node = subparsers.add_parser("get-node-id", help="Get comment node ID")
    p_node.add_argument("--comment-id", required=True)
    p_node.add_argument("--repo")
    p_node.add_argument("--token")
    p_node.add_argument("--dry-run", action="store_true")

    # get-body
    p_body = subparsers.add_parser("get-body", help="Get comment body text")
    p_body.add_argument("--comment-id", required=True)
    p_body.add_argument("--repo")
    p_body.add_argument("--token")
    p_body.add_argument("--dry-run", action="store_true")

    # post-comment
    p_post = subparsers.add_parser("post-comment", help="Post a comment")
    p_post.add_argument("--repo", required=True)
    p_post.add_argument("--issue-number", type=int, required=True)
    p_post.add_argument("--body", required=True)
    p_post.add_argument("--token")
    p_post.add_argument("--dry-run", action="store_true")

    # minimize
    p_min = subparsers.add_parser("minimize", help="Minimize a comment")
    p_min.add_argument("--node-id", required=True)
    p_min.add_argument("--token")
    p_min.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.command == "get-comments":
        comments = get_issue_comments(args.repo, args.issue_number, args.token, args.dry_run)
        print(json.dumps(comments, indent=2))
    elif args.command == "get-node-id":
        node_id = get_comment_node_id(args.comment_id, args.repo or "", args.token, args.dry_run)
        if node_id:
            print(node_id)
    elif args.command == "get-body":
        body = get_comment_body(args.comment_id, args.repo or "", args.token, args.dry_run)
        if body:
            print(body)
    elif args.command == "post-comment":
        result = post_comment(args.repo, args.issue_number, args.body, args.token, args.dry_run)
        if result.ok:
            print(result.comment_url or result.comment_id or "")
        else:
            sys.exit(1)
    elif args.command == "minimize":
        success = minimize_comment(args.node_id, args.token, args.dry_run)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
