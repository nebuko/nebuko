# GitHub Issue API Limits

> **Last updated:** 2026-10-08
>
> This document catalogs known GitHub API limits relevant to this project, which relies
> heavily on the GitHub Issues API (issue comments) for agent discussion and knowledge
> transfer.

---

## 1. Rate Limiting (REST API)

| Authentication | Limit | Window |
|---------------|-------|--------|
| Authenticated (fine-grained PAT / token) | **5,000 requests** | per hour |
| `GITHUB_TOKEN` (GitHub Actions built-in) | **1,000 requests** | per hour **per repository** |
| Unauthenticated | **60 requests** | per hour |
| Search endpoint (authenticated) | **30 requests** | per minute |
| Search endpoint (unauthenticated) | **10 requests** | per minute |

**Key details:**

- The limit resets at a fixed point each hour (not rolling).
- Response headers include `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
  `X-RateLimit-Reset`.
- **Conditional requests** (sending `If-None-Match` or `If-Modified-Since`) do **not**
  count against the rate limit when the server returns `304 Not Modified`. This is the
  single most effective way to reduce rate-limit consumption for polling workflows.

**Relevant documentation:**  
<https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api>

---

## 2. Secondary Rate Limits

Even when under the primary rate limit, GitHub may apply **secondary (abuse) rate
limits** when making too many concurrent requests or writing too quickly.

| Signal | Meaning |
|--------|---------|
| Status `403` with `Retry-After` header | Back off for the specified number of seconds |
| Status `429` | Explicit rate-limit response |
| `x-ratelimit-remaining` reaches `0` | Stop until the `x-ratelimit-reset` timestamp |
| `403` "blocked from content creation" | Content-creation burst limit exceeded |

**Content-creation limit (the one that bit us — issue #805):**

| Limit | Value | Shared with |
|-------|-------|-------------|
| Content-generating requests | **≤80 / minute** and **≤500 / hour** | REST API, GraphQL API **and the GitHub web UI** |

- *Content-generating* covers creating comments, issues, PRs, reviews and labels —
  i.e. both the ``gh``/REST writes and **every comment you type in the browser**.
- This limit is **independent of the primary quota**: you can be blocked while
  `X-RateLimit-Remaining` is still around 4,946, and the response often carries
  **no `Retry-After`** header at all.
- Because the web UI shares the same budget, a burst of parallel workflow runs that
  exhausts content creation also blocks *manual* commenting on the same account —
  the exact symptom reported in #805.

**Best practice:** Always check for the `Retry-After` header in `403`/`429` responses
and implement exponential backoff; when it is absent and the response indicates a
content-creation block, wait **at least 60 seconds** with jittered exponential
backoff.

**Relevant documentation:**  
<https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-rate-limit-errors>  
<https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api>

---

## 3. GraphQL API Rate Limits

GitHub's GraphQL API uses a **point-based** system instead of a simple request count.

| Limit | Value |
|-------|-------|
| Points per hour (authenticated) | **5,000 points** |
| Max nodes per query | **500,000** total across all connections |

Each query costs points based on complexity (checked via `rateLimit` node in the
response). This project uses GraphQL only for the `minimizeComment` mutation
(`engine/lib/github_client.py`); all comment reads and writes go through REST. The
mutation rides the same `GitHubTransport` choke point as REST, but its point cost
(≈5 points per mutation) is **not** tracked separately today.

**Relevant documentation:**  
<https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api>

---

## 4. Maximum Comment Body Size

| Limit | Value |
|-------|-------|
| Hard character limit | **65,536 characters** |
| API rejection message | `"body is too long (maximum is 65536 characters)"` |

This limit stems from GitHub's underlying MySQL `mediumblob` storage (262,144 bytes
÷ 4 bytes per UTF-8 char ≈ 65,536 characters).

**Project status:** the codebase splits long answer bodies at the
`CommentSizePolicy.max_comment_chars` threshold (default **60,000 characters**,
`config/sub_domains/comment_size/comment_size.py`), a comfortable safety margin
below the hard limit that leaves room for the bot marker and the workflow-state
header. A body longer than one comment is posted as several comments by the
publish seam (up to `max_parts`, default 4; issue #913) rather than truncated.

---

## 5. Maximum Comments Per Issue

| Limit | Value |
|-------|-------|
| REST API practical maximum | **~30,000 comments** per issue/PR per repository |

This is a documented structural limit in how GitHub stores and paginates comment
data. In practice, this project is unlikely to reach this limit — even heavy
multi-agent conversations would require thousands of rounds to exhaust the available
comment slots.

**Source:**  
<https://github.com/orgs/community/discussions/183392>

---

## 6. Pagination

| Parameter | Max Value |
|-----------|-----------|
| `per_page` (REST API) | **100 items** per page |
| Page navigation | Via `Link` header (`rel="next"`, `rel="last"`) |

**Project status:** all comment reads go through `GitHubTransport`
(`engine/lib/github_client.py`), which paginates via `gh api --paginate --slurp`. The
engine also supports a **bounded tail read** (`limit=` / `HISTORY_COMMENT_COUNT`):
it uses the `Link: rel="last"` header to jump straight to the pages covering the last
N comments, so reading the tail costs a constant number of requests regardless of
thread length (issue #805). Full-thread reads are reserved for the metrics
aggregation script.

**Relevant documentation:**  
<https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api>

---

## 7. Impact on This Project

The table below maps each limit to the code or workflow that consumes it.

| Limit | Affected Component | File / Location | Notes |
|-------|-------------------|-----------------|-------|
| 5,000 req/h rate limit (PAT / App installation token) | All engine API calls | `engine/lib/github_client.py` (`GitHubTransport`) | Every `gh api` call funnels through one choke point that tracks `x-ratelimit-remaining`/`x-ratelimit-reset`. |
| 1,000 req/h (GITHUB_TOKEN) | Dependency-install + marker `curl` steps | `.github/workflows/nebuko-workflow.yaml` | Engine steps use the run's GitHub token — an App installation token (5,000/h) or `GH_PAT` (5,000/h) in PAT fallback mode; some YAML steps still ride `GITHUB_TOKEN` (1,000/h) — unification pending. |
| Content creation (80/min, 500/h) | Comment/issue writes | `GitHubTransport._pace` + backoff | Mutative calls are paced ≥1 s apart; secondary blocks are retried with jittered backoff. See ADR-0005 for the runs/hour budget. |
| Comment body size (65,536 chars) | Multi-part message posting | `config/sub_domains/comment_size` (`CommentSizePolicy`) | Splits at 60,000 chars, up to 4 comment parts (issue #913). Safe. |
| Max comments per issue (~30,000) | Long-running issues | N/A (structural) | Unlikely to be reached in practice. |
| Pagination (100 per page) | Fetching comments | `engine/lib/github_client.py` | `--paginate --slurp` for full reads; bounded tail read for the engine's per-run snapshot. |

---

## 8. Guard policy (implemented, issue #805)

`GitHubTransport` in `engine/lib/github_client.py` is the single entry point for every
API call and implements the mitigation:

| Concern | Behaviour |
|---------|-----------|
| Header observation | `gh api --include` output is parsed for `x-ratelimit-remaining`, `x-ratelimit-reset` and `retry-after`. |
| Failure classification | `ApiErrorKind`: `PRIMARY_RATE_LIMIT`, `SECONDARY_RATE_LIMIT`, `AUTH`, `NOT_FOUND`, `TRANSIENT`, `DECODE`, `UNKNOWN`. |
| Backoff | Retryable kinds are retried up to `max_attempts` with jittered exponential backoff; `Retry-After` / `x-ratelimit-reset` are honoured; a secondary block without either defaults to a 60 s cooldown. |
| Write pacing | Mutative calls are spaced ≥1 s apart (GitHub's documented pause between mutative requests). |
| Honest results | Writes return `PostCommentResult`; a dropped write raises `PublishError` in the adapter instead of silently succeeding. |

For the per-run capacity budget (how many workflow runs/hour the default 7-agent flow
affords), see **[ADR-0005 — GitHub API budget](./adr/0005-github-api-budget.md)**.

---

## 9. Recommendations

### 9.1 — Conditional requests for fetching comments (Low Priority)

When fetching issue comments, include the `If-None-Match` header with the ETag from
the previous response. If comments haven't changed, the server returns `304 Not
Modified`, which does **not** consume rate limit.

### 9.2 — Consider GraphQL for bulk operations (Future)

If the project ever needs to fetch all comments across multiple issues efficiently,
switching to the GraphQL API would reduce the number of requests needed (at the cost
of higher per-query complexity).

---

## References

1. [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
2. [Troubleshooting rate limit errors](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-rate-limit-errors)
3. [GraphQL API rate limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api)
4. [Community discussion: Max comments per issue](https://github.com/orgs/community/discussions/183392)
5. [Community discussion: Max comment body size](https://github.com/orgs/community/discussions/27190)
6. [Community discussion: Comment length error](https://github.com/orgs/community/discussions/41331)
7. [Using pagination in the REST API](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api)
8. [Best practices for using the REST API](https://docs.github.com/en/rest/guides/best-practices-for-using-the-rest-api)

Related design record: [ADR-0005 — GitHub API budget](./adr/0005-github-api-budget.md)
(runs/hour ceiling for the default 7-agent flow).