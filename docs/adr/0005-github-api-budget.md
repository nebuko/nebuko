# ADR-0005: GitHub API Budget — Content-Creation Ceiling and the Per-Run Cost Model

**Status:** Accepted — 2026-10-08
**Deciders:** Olivier Norture (author), @architect (architecture)
**Related:** #805 (this change), #648 (silent lost answer), PR #814

## 1. Context

A job for issue #648 succeeded while the agent's answer never appeared on the
thread. The job log showed:

```
INFO:nebuko.engine.run_loop:[phase:publish] staged run logs in /runner/_work/_temp/nebuko-task-logs
Using latest answer (9242 characters)
Error posting comment: unexpected end of JSON input
INFO:nebuko.engine.run_loop:[phase:state]
INFO:nebuko.engine.run_loop:[phase:route]
```

Three facts were established during the investigation:

1. `unexpected end of JSON input` is emitted by the **`gh` CLI**, not by Python:
   `gh api` unconditionally `json.Unmarshal`s the response body, so a 403 whose
   body is empty dies as a decode error and the real HTTP status is never seen
   (cli/cli#10579, cli/go-gh#161).
2. `post_comment` returned `None` on that failure, `GitHubAdapter.post` coerced
   it to `""`, and `_run_phase("publish")` still reported `ok=True` — the write
   was **dropped silently** and the run exited 0. The same failure shape is
   documented at ebowwa/dsh-agent-toolkit#537.
3. Simultaneously, **manual** comment creation through the GitHub web UI began
   failing. That is only explicable by an account-level limit, because the web UI
   shares GitHub's **content-creation secondary rate limit** with the API
   (≤80 content-generating requests/minute and ≤500/hour, "some endpoints have
   lower limits"; community report orgs/community#32120 shows such blocks firing
   while `X-RateLimit-Remaining` is still ~4,946, sometimes without `Retry-After`).

The burst that triggered it — many workflows launched in parallel, each posting
its start notice and its answer — is exactly the "create too much content in a
short amount of time" pattern. Before this change the codebase had **zero**
rate-limit awareness: `grep -rni "retry-after|x-ratelimit|429|backoff"` over
`src/nebuko` and `.github/workflows` returned no matches, every error path
printed to stderr and returned `[]`/`None`/`False`, and the context build
re-fetched the **entire** comment thread at 4+ independent sites per run.

## 2. Decision

1. **One choke point.** Every `gh` invocation goes through `GitHubTransport`
   (`src/nebuko/engine/lib/github_client.py`), which captures
   `gh api --include` headers, classifies failures (`ApiErrorKind`), honours
   `Retry-After` / `x-ratelimit-reset` with **bounded jittered exponential
   backoff**, and paces mutative calls **≥1 s apart** (GitHub's documented
   pause between mutative requests).
2. **Honest writes.** A comment that cannot be delivered after retries raises
   `PublishError`; the run records `publish_failed`, stages the undelivered body
   as an artifact and emits a job annotation — never a green run with no answer.
3. **Bounded reads.** A per-run `ThreadSnapshot` fetches the thread **at most
   once**, tail-bounded by `HISTORY_COMMENT_COUNT` via a last-page jump: a
   constant number of requests for any thread length.
4. **The content-creation limit is the budget we plan against** — not the 5,000
   req/hr primary limit. The capacity model below is the operating contract.

## 3. Capacity model — the default flow

Assume the **default** workflow at **7 agent runs per issue**, each producing
**2 content-generating writes** (one "`<role>` agent started" notice + one
answer comment carrying the workflow state) = **14 content writes per run**.

| Budget | Limit | Per-run cost | Runs/hour ceiling |
|---|---|---|---|
| **Content creation (secondary)** — *binding* | **500/hr**, 80/min | **14 writes** | **≈ 35 runs/hr** (500 ÷ 14) |
| Same, with 20 % safety margin | — | 14 writes | **≈ 28 runs/hr** (recommended operating maximum) |
| Content creation, per-minute burst | 80/min | 14 writes | ≈ **5.7 runs/min** |
| Primary REST (PAT, `GH_PAT`) | 5,000/hr | ~14 writes + ~10 reads ≈ 24 req | ≈ 208 runs/hr (not binding) |
| `GITHUB_TOKEN` (if content writes ride it) | 1,000/hr/repo | ~7–8 writes | ≈ 125–143 runs/hr (avoid: unify on the PAT) |
| GraphQL points (`minimize_comment`) | 5,000 pts/hr | 5 pts per mutation | negligible unless minimisation is abused |
| Concurrent requests (secondary) | 100 | N parallel runs | relevant only at high fan-out; pacing + jitter handle it |

Reading of the table:

- **≈ 35 runs/hour is the hard ceiling** for this repo's default flow, and the
  practical target is **≈ 25–28 runs/hour** to leave headroom for the web UI,
  issue creation, PR creation and label writes, which all draw on the same
  500/hour content budget (PRs, issues and labels are content-generation too).
- The **80/minute** cap is the burst trap: **six or more runs whose comment
  bursts land in the same minute** exceed it even while the hourly budget looks
  untouched. This is the incident's mechanism, and it is why the transport
  enforces ≥1 s spacing between writes and jitters its backoff — a blocked job
  backs off on its own, so the fleet de-synchronises instead of retrying in
  lockstep.
- **Reads are no longer a multiplier.** Before: 4+ sites × ⌈comments/100⌉
  requests per run (a 300-comment thread cost ≥12 requests, growing with every
  posted answer). After: **1–2 requests per run, independent of thread length**
  (one discovery request plus the tail page when the thread spans pages). The
  primary budget therefore stops being a plausible binding constraint.
- **Burst concurrency is the remaining risk.** Nothing in the engine can
  coordinate across runners (they share no state), so the fleet-level defence is
  reactive backoff plus the operating guidance below. A cross-job queue/lock is
  explicitly out of scope (see §5).

### 3.1 Operating guidance for the fleet

- Treat **`x-ratelimit-remaining < 100`** as "slow down"; the transport already
  waits out the window below its configured threshold.
- Stagger parallel launches rather than firing N runs at once: the per-minute
  content cap allows ≈5 runs/minute.
- Budget manual UI activity against the same 500/hour: bulk triage, issue
  creation and commenting from the web UI compete with the fleet.
- When a secondary block is hit without `Retry-After`, the transport waits its
  fallback cooldown and retries with jitter; repeated blocks mean the launch rate
  is above ≈28 runs/hour.

## 4. Alternatives considered and rejected

1. **Patch each call site ad hoc** (raise in `post_comment`, sprinkle retry
   loops) — N sites re-implementing backoff inconsistently; leaves the
   context-fetch multiplier untouched.
2. **Replace `gh` with a native HTTP client** — maximum control over headers and
   status codes, but a blast radius across every call site and all tests. Kept as
   a future drop-in swap *behind* the transport interface.
3. **A shared cross-job queue / lock for writes** — runners share no state; would
   need a coordination service for a problem that pacing + jitter + volume
   reduction already addresses.
4. **Change the comment-based state channel** (labels or issue body) — the
   snapshot + tail-bounded fetch removes the multiplier without re-architecting
   the workflow-state contract.

## 5. Consequences

- The incident class in #805/#648 becomes impossible: a comment either posts
  (after bounded backoff) or the run surfaces a hard error with the answer
  preserved and `publish_failed` recorded — never "green job, no answer".
- Writes are slower by design (≥1 s spacing). A run posting several comments pays
  a few seconds; in exchange the fleet degrades into queued retries instead of
  silent drops.
- Reads drop to a bounded per-run cost, so the primary 5,000 req/hr budget is no
  longer a design constraint; the content-creation ceiling is.
- Backoff is bounded (4 attempts, cap 60 s), so a hard block eventually surfaces
  as a failed publish rather than an unbounded hang.
- **Non-goals of v1:** fleet-wide coordination, native HTTP client, changing the
  state channel, and moving issue/PR creation off the REST API.

## 6. Future work

- Unify the remaining clients (`issue_creation.GitHubClient` over raw `urllib`,
  `ensure_pr`) onto the transport, and route the workflow YAML's raw `curl`
  content writes off `GITHUB_TOKEN` onto the PAT budget so one policy governs
  every write.
- Revisit a native HTTP client if `gh`'s decode behaviour keeps masking real
  HTTP statuses.
- If the fleet grows past ≈28 runs/hour, add a cross-job write queue (or
  per-identity token partitioning) rather than raising in-process concurrency.
