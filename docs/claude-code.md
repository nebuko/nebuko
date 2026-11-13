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
an empty value can never shadow a token the runner already provides.

Then comment `/nebuko run workflow --name default` on an issue.

> **Billing.** A subscription token bills the seat that minted it. Only use it
> in a repo where you are the triggerer; for shared/org automation use
> `ANTHROPIC_API_KEY` (Console) instead. The reasoning, the rejected
> alternatives and the compliance rules are in
> [ADR-0017](./adr/0017-claude-code-subscription-credential-strategies.md).

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
    backend: "claude-code"             # default: "openhands"
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
