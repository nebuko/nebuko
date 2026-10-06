# GitHub Issue API Limits

> **Last updated:** 2026-06-05
>
> This document catalogs known GitHub API limits relevant to this project, which relies
> heavily on the GitHub Issues API (issue comments) for agent discussion and knowledge
> transfer.

---

## 1. Rate Limiting (REST API)

| Authentication | Limit | Window |
|---------------|-------|--------|
| Authenticated (token/PAT) | **5,000 requests** | per hour |
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

**Best practice:** Always check for the `Retry-After` header in `403`/`429` responses
and implement exponential backoff.

**Relevant documentation:**  
<https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-rate-limit-errors>

---

## 3. GraphQL API Rate Limits

GitHub's GraphQL API uses a **point-based** system instead of a simple request count.

| Limit | Value |
|-------|-------|
| Points per hour (authenticated) | **5,000 points** |
| Max nodes per query | **500,000** total across all connections |

Each query costs points based on complexity (checked via `rateLimit` node in the
response). This project does **not** currently use the GraphQL API, but it is an
alternative for more efficient comment fetching if the REST API rate limit becomes a
bottleneck.

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

**Project status:** The codebase currently splits long messages at **10,000
characters** (`MAX_MESSAGE_LENGTH` in `agent_script.py`). This provides a comfortable
safety margin below the hard limit, leaving room for bot markers and metrics
commentary appended during posting.

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

**Project status:** `summarize_metrics.py` already uses `per_page=100` and iterates
through paginated results when fetching issue comments. This is the optimal page size
for this endpoint.

**Relevant documentation:**  
<https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api>

---

## 7. Impact on This Project

The table below maps each limit to the code or workflow that consumes it.

| Limit | Affected Component | File / Location | Notes |
|-------|-------------------|-----------------|-------|
| 5,000 req/h rate limit | All API calls | `engine/lib/summarize_metrics.py`, `engine/lib/agent_script.py`, `.github/workflows/nebuko-workflow.yaml` | Each agent run does several API calls (fetch comments, post comment, update check runs). With 5 agents, a single issue round could consume 5–15 requests. |
| Comment splitting (65,536 chars) | Multi-part message posting | `agent_script.py` (lines 355–403) | Splits at 10,000 chars. Safe. |
| Max comments per issue (~30,000) | Long-running issues | N/A (structural) | Unlikely to be reached in practice. |
| Pagination (100 per page) | Fetching comments | `summarize_metrics.py` (lines 29–60) | Correctly uses `per_page=100` with pagination. |
| Secondary rate limits (Retry-After) | Comment posting, comment fetching | Not handled anywhere | **Gap — not currently implemented.** |
| Conditional requests (304) | Comment fetching | Not handled anywhere | **Gap — not currently implemented.** |

---

## 8. Recommendations

### 8.1 — Handle Secondary Rate Limits (Medium Priority)

Add `Retry-After` header detection and exponential backoff to `summarize_metrics.py`
and `agent_script.py`. When a `403` or `429` response includes a `Retry-After` header,
the affected function should wait the specified number of seconds before retrying.

### 8.2 — Use Conditional Requests for Fetching Comments (Low Priority)

When fetching issue comments, include the `If-None-Match` header with the ETag from
the previous response. If comments haven't changed, the server returns `304 Not
Modified`, which does **not** consume rate limit. This is most valuable for the
metrics summary step, which may poll the same issue multiple times.

### 8.3 — Monitor Rate-Limit Headers (Low Priority)

Log `X-RateLimit-Remaining` and `X-RateLimit-Reset` on every API call. If the
remaining count drops below a threshold (e.g., 100), emit a warning. This gives
operators visibility before a hard block occurs.

### 8.4 — Consider GraphQL for Bulk Operations (Future)

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