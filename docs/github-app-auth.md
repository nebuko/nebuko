# GitHub App tokens: why agent-start comments were delayed (issue #1220)

## What you see

With the GitHub App installed, the engine's acknowledgment comment on your
issue appears **several minutes after the job starts**. With a PAT
(`GH_PAT`) it appears within seconds.

## Why it happened

The engine resolves *who it posts as* at the start of every run, via
`GET /user` (`github_client.get_authenticated_user`). This works with a PAT,
which always belongs to a user account.

A GitHub App **installation token** is an *integration token*. GitHub refuses
`GET /user` for it with:

```
403 Resource not accessible by integration
```

The transport's error classifier (`GitHubTransport._classify`) maps any bare
403 to `SECONDARY_RATE_LIMIT` — a retryable, rate-limit-shaped failure. So in
App mode the engine retried the identity probe four times, honouring GitHub's
"wait at least one minute" secondary-rate-limit cooldown between attempts
(~3 minutes total), and only then failed closed to the anonymous actor
`(0, "")` and continued. The run wasn't slow; the very first API call was
sitting out a cooldown that never applied.

## How it works now

1. **Detection** — `github_client.classify_integration_denial(result)`
   recognizes the integration-denial signature: a failed `403` whose body is
   "Resource not accessible by integration" and which carries **no**
   rate-limit signature (`x-ratelimit-remaining: 0`, "rate limit exceeded",
   "secondary rate limit"). Rate-limit 403s remain retryable.
2. **Classification** — `GitHubTransport._classify` returns
   `ApiErrorKind.AUTH` for an integration denial. `AUTH` is in the
   non-retryable set, so the call fails on the first attempt with no sleep.
3. **Fallback** — `adapters/github.py::engine_actor()` already treats an
   unresolvable identity as best-effort: it returns `(0, "")` and the ledger
   fails closed. The difference is that this now takes milliseconds, not
   minutes, so `notify_agent_started()` posts immediately.

## What this means for App users

- Agent-start comments post as fast as in PAT mode.
- The engine actor stays anonymous under the App (installation tokens have no
  user identity — that remains the `nebuko[bot]`/real-account question
  tracked in issue #1218).
- No configuration change is needed: the fix is in the transport's error
  classification, shared by PAT and App modes.
