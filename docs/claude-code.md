# Claude Code backend

A role can run through [Claude Code](https://claude.com/claude-code) instead of
the OpenHands model loop. Claude Code brings its own tools, context management
and model calls; the engine around it (workflow routing, the closing report,
publication, hooks) stays Nebuko's. You can bill your own Claude subscription or
an API key.

The runtime is the official `claude-agent-sdk`, which drives the plain `claude`
binary in headless mode and hosts an **in-process MCP server** for the engine's
tools — no sidecar, no out-of-process bridge. See
[architecture.md § Harness Port](./architecture.md#harness-port-issue-1368) for
the seam itself.

## Quick start (hosted runner)

1. Mint a long-lived subscription token with `claude setup-token` and copy it.
2. Store it as the repository secret `CLAUDE_CODE_OAUTH_TOKEN`
   (*Settings → Secrets and variables → Actions*).
3. Start from the tracked profile
   [`agent-config-anthropic.yaml`](../.nebuko/agents/agent-config-anthropic.yaml)
   (every role on `backend: "claude-code"`), or switch individual roles as
   shown below.

The reusable workflow declares the secret and exports it into the job
environment **only when it is non-empty** (`::add-mask::`ed, never echoed), so
an empty value can never shadow a token the runner already provides. The
comment trigger workflow forwards it (`secrets: CLAUDE_CODE_OAUTH_TOKEN`), and
the engine removes it from the environment of every backend except
`claude-code` before the agent is built — an OpenHands run in the same workflow
never has it, so a prompt-injected agent cannot exfiltrate it through the
`terminal` tool.

Then comment `/nebuko run workflow --name default` on an issue.

> **Every agent that uses the claude-code provider must say
> `backend: "claude-code"`.** That covers `CLAUDE_CODE_OAUTH_TOKEN` *and* a
> `claude-code/*` model path, including `complexity_overrides.<tier>.model`
> swaps. The default backend is `openhands`, so an agent that only points its
> model at the provider is a config error. A subscription token may only be
> exercised by the unmodified Claude Code binary (ADR 0017 rule 1); running it
> through the OpenHands/litellm loop risks the account being banned. It is
> enforced twice: `lint-config` rule
> [`C12`](./lint.md#c12-claude-credentials--subscription-tokens-are-binary-only)
> and a terminal run-time refusal (`ClaudeCredentialError`) before any provider
> call. The metered `ANTHROPIC_API_KEY` lane is fine with `claude-code/*` model
> paths. Detection is by env-var name and model prefix, so an unusual spelling
> can slip through (see #1394).

> **Billing.** A subscription token bills the seat that minted it. Only use it
> in a repo where you are the triggerer; for shared/org automation use
> `ANTHROPIC_API_KEY` (Console) instead. The reasoning, the rejected
> alternatives and the compliance rules are in
> [ADR-0017](./adr/0017-claude-code-subscription-credential-strategies.md).

## Per-user credential repo (`nebuko init claude-code-repo`)

To keep your subscription token out of a shared repository, run the setup
command from a terminal where `gh` is logged in as the account that owns the
runs:

```bash
nebuko init claude-code-repo               # --pin-ref <ref> (default: main)
```

It creates a private `<your-login>/nebuko-claude-code` repository, asks for
your `claude setup-token` value and stores it only as that repo's
`CLAUDE_CODE_OAUTH_TOKEN` secret (piped to `gh secret set`; Nebuko never stores
or logs it), writes a thin workflow pinned to the reusable workflow's commit
SHA, and prints the GitHub App installation link to finish the setup.

The generated workflow refuses to run unless the triggering actor is the
repository owner, so only you can start a run in that repo. An existing repo is
refused before anything is written; a missing token creates nothing.

## Lane D: per-user repo (manual dispatch and troubleshooting)

Operator notes for the per-user repo above, from the manual dispatch runs on
`olivier-norture/nebuko-claude-code` (runs 44302540158 … 44332921102, tracked in
#1388 and #1099). Until the bridge is deployed, every run is started by hand.

### Setup

- Run `nebuko init claude-code-repo` (optional `--pin-ref <ref>`, default `main`)
  where `gh` is logged in as the repo owner.
- The token comes from the `CLAUDE_CODE_OAUTH_TOKEN` env var if set, otherwise
  from a hidden prompt. Produce it with `claude setup-token`.
- The command refuses if `<login>/nebuko-claude-code` already exists.
- Install the GitHub App on that repo only:
  <https://github.com/apps/nebuko/installations/new>. This is needed for the
  bridge, **not** for the manual dispatch below.

### Workflow template

`.github/workflows/nebuko-run.yaml` is rendered by `render_workflow`:

| Field | Value |
|-------|-------|
| `on` | `repository_dispatch`, type `nebuko-run` |
| `owner-guard` job | asserts `client_payload.actor == github.repository_owner` (guard 4) |
| `with:` `prompt_body` | `client_payload.prompt_body` |
| `with:` `org_repo` | `client_payload.org_repo` |
| `with:` `issue_number` | `client_payload.issue_number` |
| `with:` `agent_config_path` | `agent-config-anthropic.yaml` |
| `secrets:` `CLAUDE_CODE_OAUTH_TOKEN` | the user repo's own secret |
| `secrets:` `GH_PAT` | `client_payload.gh_token` (org-repo token, see below) |

- **Pin.** `uses:` must name a full 40-character commit SHA, because `uses:`
  cannot track a branch. `render_workflow` rejects anything else.
- **Bump.** There is no bump command. Resolve the new SHA with
  `gh api repos/nebuko/nebuko/commits/main --jq .sha`, edit the `@<sha>` in
  `nebuko-run.yaml`, and commit. Re-running `init` will not help, because it
  refuses an existing repo.

### Manual dispatch

The issue did not record a literal command, so this call is built from the
ADR-0017 payload and is **unverified against a live run**:

```bash
gh api --method POST repos/<login>/nebuko-claude-code/dispatches \
  -f event_type=nebuko-run \
  -f client_payload[actor]=<login> \
  -f client_payload[org_repo]=<org>/<repo> \
  -F client_payload[issue_number]=<number> \
  -f client_payload[prompt_body]="<prompt>" \
  -f client_payload[gh_token]="<org-repo token>"
```

- `actor` must equal the repo owner, or guard 4 fails the run.
- **Warning:** the org-repo token in `gh_token` is visible to the repo owner in
  the run. Use a short-lived token (see below).

### Org-repo token requirements

Until the bridge mints the token:

- Fine-grained token, resource owner = the org, scoped to the target repo only.
- Permissions: Issues, Pull requests and Contents, read and write.
- The org may need to approve the token before it works.
- Fallback: a classic `repo` PAT. It needs SSO authorization for the org.
- Use a 1-day expiry and revoke the token after the run.
- See [adoption.md](./adoption.md) and [nebuko-init.md](./nebuko-init.md) for
  the general setup; they are not repeated here.

### Troubleshooting

Run links are not recorded for each symptom: the runs are on the user's repo,
which this docs pass could not read. Each row cites the issue or PR that
produced the fix or the finding.

| Symptom | Cause | Source |
|---------|-------|--------|
| `run-task` skipped, run is green | Workflow predates #1426, or the event is not `repository_dispatch` `nebuko-run` | #1426; run link not recorded |
| `Bad credentials` at checkout | `gh_token` is invalid, expired or truncated | run link not recorded |
| 404 on comments, issue or labels | Token not scoped to the org repo, or engine older than #1429 / #1431. The log line `[phase:repo] target repo ...` shows which repo the engine uses | #1429, #1431; run link not recorded |
| `Failed to authenticate ... 401 OAuth access token is invalid` | `CLAUDE_CODE_OAUTH_TOKEN` expired or revoked; rotate it with `claude setup-token` | run link not recorded |
| `refusing to push the base branch` | Dispatch-branch issue | #1432 |

### Known limits

Until the follow-ups land:

- Continuation steps do not stay on the user repo (credential-bound
  continuation design, #1433).
- The bridge is not deployed (token-broker#53).
- Per-run Actions minutes and the 6 h job cap apply to the user's repo.

_Update this section after #1432 and #1433 are resolved._

### Credential rules

- The secret lives only in the user's repo. Never store it in a shared repo.
- Guard 3 (`nebuko-workflow.yaml`): schedule, actor-less and bot-started runs
  are refused. Bot continuations are allowed only under the #1427 rule.
- Guard 4 (`owner-guard` job): asserts `client_payload.actor` equals the
  repository owner before any job that can reach the token.
- Never use the shared org token for a user-started workflow. The repo owner
  can read it during the run.

## Per-role switch

```yaml
providers:
  claude-code:
    api_url: "https://api.anthropic.com"
    api_token_env: "CLAUDE_CODE_OAUTH_TOKEN"

llms:
  claude-sonnet:
    model_path: "claude-code/sonnet"   # last segment = Claude Code model alias
    provider: "${providers.claude-code}"

agents:
  dev:
    backend: "claude-code"             # required for claude-code/* models
    model: "${llms.claude-sonnet}"
```

Roles can be mixed: only the ones that say `backend: claude-code` use it. The
last segment of the model's `model_path` is the Claude Code model alias
(`sonnet`, `opus`, `opus[1m]`, `haiku` or a full id such as `claude-opus-5`).
`api_url` is unused by this harness; only `api_token_env` matters.

## How a step runs

| Stage | OpenHands backend | Claude Code backend |
|-------|-------------------|---------------------|
| Work loop | SDK `Agent`, Nebuko tools | `claude-agent-sdk` drives the headless `claude` binary; Claude Code's own tools |
| System prompt | Nebuko's static prompt | Claude Code's own, plus `CLAUDE.md` / `AGENTS.md` and a short engine preamble |
| Dynamic context (team, exit rules) | system suffix | same suffix, sent with the user message |
| Engine tools | native SDK tools | in-process MCP server, surfaced as `mcp__nebuko__*` |
| Verdict and report | `finalize` tool call | `mcp__nebuko__finalize` tool call in a dedicated closing turn |
| Missing verdict | dispatch recall | same bounded recall passes |
| Timeout | watchdog + wind-down | same: the watchdog interrupts the session, never closes it |

The closing turn narrows the tool surface to `mcp__nebuko__finalize` (enforced
by a `PreToolUse` hook) and asks for the same `turn_outcome` the `finalize` tool
takes (`appreciation`, `hold_kind`, `agent_call`, `round`, `note`, `complexity`,
`pin`); it is validated and recorded identically on both backends.

## Differences to know about

- `temperature`, sampling and `token_budget` are ignored: Claude Code owns its
  tools and compacts its own context.
- A role's declared `tools` map onto the Claude Code built-ins that serve the
  same role (`terminal`→`Bash`, `read`→`Read`, `edit`/`file_editor`→`Edit`,
  `write`→`Write`, `task_tracker`→`TodoWrite`); `think` and `finish` have no
  equivalent. The engine tools (`finalize`, `terminal_batch`,
  `complexity_review`, `fetch_comment_bodies`, `report_scaffold_branch`) are
  served by the in-process MCP server.
- `discover`-mode skills are a config error on this backend (the SDK's skill
  catalog is not wired); `inject`-mode skills still travel in the prompt.
- Declared `mcp_servers` become SDK stdio configs, so MCP tokens keep flowing
  through the same secret slots.
- The permission mode is `acceptEdits`: Claude Code can edit files and run
  commands in the checkout. Run it on a disposable runner, not a machine you
  care about.
- The SDK ships the `claude` binary in its wheel, so no Node.js or `npx`
  install is needed — `uv sync --extra runtime` is enough.
- The live `claude` smoke test is opt-in (`NEBUKO_LIVE_CLAUDE=1`) and never
  runs in CI.

## Self-hosted runners

Running on your own machine, with the token kept in the runner's private `.env`
instead of a GitHub secret (ADR-0017's Lane R), is **not implemented yet** —
`nebuko init anthropic-local-runner` is tracked as a follow-up (#1393). Until
then use the hosted path above, or a normal `runs_on: "self-hosted"` runner that
exports `CLAUDE_CODE_OAUTH_TOKEN` in its own environment.

## Runner strategy (`runner_strategy`)

The reusable workflow's `runner_strategy` input picks where jobs run:

- `static` (default): jobs run on `runs_on`.
- `per-user`: jobs run on a runner labelled `nebuko-<event author login>`, so
  each user's Claude Code token stays in that user's runner environment.

The workflow refuses these combinations with an error:

- `per-user` together with the shared `CLAUDE_CODE_OAUTH_TOKEN` secret.
- `per-user` with a static `runs_on` starting with `nebuko-`.
- The claude-code backend (a `CLAUDE_CODE_OAUTH_TOKEN` is passed) for bot,
  schedule or actor-less events. Use the API-key lane for those runs.

Credentials reach steps through `env`, never through `with:` inputs (ADR-0008).
A caller-provided `GH_PAT` is used directly and skips the OIDC token broker.
