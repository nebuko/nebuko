# Claude Code backend

A role can run through [Claude Code](https://claude.com/claude-code) instead of
the OpenHands model loop. Claude Code brings its own tools, context management
and model calls, and the engine around it (workflow routing, the closing report,
publication, hooks) stays Nebuko's. You can bill your own Claude subscription
or an API key.

## Quick start (your own machine)

```bash
nebuko init anthropic-local-runner --repo owner/name
```

This:

1. asks for a long-lived token (`claude setup-token` prints one) with hidden
   input, or reads `NEBUKO_SECRET_CLAUDE_CODE_OAUTH_TOKEN`;
2. registers `--count N` GitHub Actions runners (default 1) under
   `~/.nebuko/runner/`, labelled `nebuko-<your-github-login>`, and installs them
   as services (`--no-service` prints the `run.sh` command instead);
3. keeps the token in each runner's private `.env` (mode `0600`): it is **never**
   stored as a GitHub secret;
4. opens the usual `nebuko init` pull request with the `claude-code` profile and
   `runs_on: "nebuko-<login>"` (`--no-config` skips it).

Then comment `/nebuko run workflow --name default` on an issue: the job runs on
your machine. `nebuko init anthropic-local-runner --repo owner/name --remove`
unregisters and deletes the runners.

Requirements on the machine: `git`, `gh` (logged in with admin rights on the
repository), and Node.js (`npx` fetches the pinned `claude-agent-acp`).

## Using a hosted runner instead

Copy [`agent-config-claude-code.yaml`](../defaults/agents/agent-config-claude-code.yaml)
(or `nebuko init --provider claude-code`) and store `CLAUDE_CODE_OAUTH_TOKEN` as a
repository secret. The workflow exports it only when it is set, so a
self-hosted runner's own token is never shadowed by an empty value.

## Per-role switch

```yaml
agents:
  dev:
    backend: "claude-code"        # default: "openhands"
    model: "${llms.claude-sonnet}" # model_path "claude-code/sonnet" -> alias "sonnet"
```

Roles can be mixed: only the ones that say `backend: claude-code` use it. The
last segment of the model's `model_path` is the Claude Code model alias
(`sonnet`, `opus`, `opus[1m]`, `haiku` or a full id).

## How a step runs

| Stage | OpenHands backend | Claude Code backend |
|-------|-------------------|---------------------|
| Work loop | SDK `Agent`, Nebuko tools | SDK `ACPAgent` runs Claude Code; its own tools |
| System prompt | Nebuko's static prompt | Claude Code's own, plus `CLAUDE.md` / `AGENTS.md` and a short engine preamble |
| Dynamic context (team, exit rules) | system suffix | same suffix, sent with the user message |
| Verdict and report | `finalize` tool call | closing turn: report text, then one fenced ```` ```nebuko-turn-outcome ```` JSON block |
| Missing verdict | dispatch recall | one corrective retry of the closing turn |
| Timeout | watchdog + wind-down | same: interrupt cancels the ACP turn |

The closing-turn JSON is the same `turn_outcome` the `finalize` tool takes
(`appreciation`, `hold_kind`, `agent_call`, `round`, `note`, `complexity`,
`pin`) and is validated and recorded identically.

## Differences to know about

- `tools`, `temperature`, sampling and `token_budget` are ignored: Claude Code
  owns its tools and compacts its own context.
- Nebuko-only tools (`terminal_batch`, `report_scaffold_branch`,
  `complexity_review`, handoff fold) are not available to Claude Code.
- `discover`-mode skills need the SDK `invoke_skill` tool and are not attached;
  `inject`-mode skills still travel in the prompt.
- The permission mode is `bypassPermissions`: Claude Code can run any command
  in the checkout. That is why `anthropic-local-runner` refuses a **public**
  repository unless `--allow-public` is given, and why the runner should run
  as an unprivileged user.
- A runner that is offline leaves the job queued; there is no automatic
  fallback to a hosted runner yet.
- Check Anthropic's terms for using a subscription token from automation.
