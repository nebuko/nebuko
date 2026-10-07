# Getting Started

Set up nebuko in your repository and run your first multi-agent workflow.
This page assumes you know basic GitHub Actions; for the underlying concepts,
read [architecture.md](./architecture.md) and
[workflows.md](./workflows.md).

---

## Prerequisites

1. **A GitHub Actions runner** — the orchestrator runs on GitHub-hosted
   runners by default (`runs-on: ubuntu-latest`), so no setup is required.
   To run on your own infrastructure instead, pass `runs_on: "self-hosted"`
   to the reusable workflow and [set up a self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners).
2. **Python 3.13** and the **`uv`** package manager on the runner.
3. **GitHub secrets** configured at repo or organization level:

   | Secret | Required | Purpose |
   |--------|----------|---------|
   | `OPENROUTER_API_KEY` | yes | LLM provider token (OpenRouter). |
   | `GH_PAT` | yes | GitHub PAT with `repo` scope — used for git push, issue/PR API calls, and posting comments. |
   | `LOCAL_VLLM_API_TOKEN` | optional | Only if you use a local vLLM provider. |
   | `COMMANDCODE_API_KEY` | optional | Only if you point the agents at a CommandCode subscription ([subscription-plans.md](./subscription-plans.md)). |

4. **A configuration file** — `agent-config.yaml` (see step 2 below) and agent
   prompts (step 3), committed to your repository.

---

## Step 1 — Add the Workflow

Copy the consumer file into `.github/workflows/workflow.yaml`:

```yaml
name: AI Dev Workflow (nebuko)

on:
  issues:
    types: [opened, edited, assigned, closed]
  issue_comment:
    types: [created]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  agent:
    uses: nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@main
    with:
      agent_config_path: "agent-config.yaml"
      circuit_breaker_threshold: 15
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      GH_PAT: ${{ secrets.GH_PAT }}
```

This thin wrapper calls the reusable orchestrator from this repository — you
never vendor the 900-line workflow file itself (zero-copy, see
[features.md — Remote Workflows](./features.md#remote-workflows-zero-copy)).
Optional inputs: `runs_on` (default `ubuntu-latest`; e.g. `self-hosted` to use
your own runners), `summarizer_threshold` (default 100), `pin_comment` (default
`false`; pin every agent step output for verbatim context injection),
`python_version` (default `3.13`), `enable_uv_cache` (default `true`),
`prompt_body`.

---

## Step 2 — Create `agent-config.yaml`

Commit a layered config (providers → llms → agents) at the repo root. Minimal
working example with 3 agents:

```yaml
providers:
  openrouter:
    api_url: "https://openrouter.ai/api/v1"
    api_token_env: "OPENROUTER_API_KEY"

llms:
  my-model:
    model_path: "openrouter/deepseek/deepseek-chat-v3-0324"
    provider: "${providers.openrouter}"
    default_temperature: 0.7

agents:
  genius:
    model: "${llms.my-model}"
    temperature: 1.0
    prompt_file: ".agents/prompts/genius.md"
    tools: ["think", "finish"]

  dev:
    model: "${llms.my-model}"
    prompt_file: ".agents/prompts/dev.md"
    tools: ["terminal", "file_editor", "task_tracker", "think", "finish"]

  review:
    model: "${llms.my-model}"
    prompt_file: ".agents/prompts/review.md"
    tools: ["think", "finish"]
```

For the full schema (all fields, `${ref}` rules, sampling parameters), see
[configuration.md](./configuration.md). You can start from this repository's
[`agent-config.yaml`](../agent-config.yaml) as a reference.

Using a subscription plan instead of a metered API key? See
[subscription-plans.md](./subscription-plans.md) for the provider stanza, the
`COMMANDCODE_API_KEY` secret, and a working example config.

---

## Step 3 — Create Agent Prompts

Place one Markdown system prompt per agent under `.agents/prompts/`:

```
.agents/prompts/
├── genius.md
├── dev.md
└── review.md
```

Each file describes the agent's role, responsibilities, output contract (how it
must emit its `AGENT_RUN_CONFIG` dispatch — see
[reference.md](./reference.md#agent_run_config)), and constraints. You can start
with minimal prompts and refine them iteratively; the prompts in this repo's
[`.agents/prompts/`](../.agents/prompts/) are good starting templates.

---

## Step 4 — First Trigger

1. Open an issue in your repository.
2. In the issue body (or a comment), type:

   ```
   @@genius <your request, e.g. "add input validation to the parser">
   ```

That's it. The trigger condition matches the `@@genius` mention, the
orchestrator runs, and the `default` workflow starts
(see [workflows.md — default](./workflows.md#default)).

---

## What to Expect

You'll see a sequence of comments on the issue:

1. **Acknowledgment** — a short bot comment confirming the run started (it is
   minimized after the agent responds).
2. **Agent responses** — one comment per agent step, each tagged with a
   `<!-- bot-agent-{name} -->` marker, ending with the next-step dispatch.
3. **Workflow state** — a collapsed JSON section
   (`<!-- WORKFLOW_STATE_START -->`) inside each comment so you can inspect
   exactly where the chain is.
4. **Result** — for the `default` workflow: analysis → context report → plan →
   implementation (with a PR opened by `@@dev`) → review verdict.

Where to look when something goes wrong:

- **GHA run logs** — the run for the triggering event, job `run-task`.
- **Run artifacts** — each run publishes `console.log` and `app.log` as separate unzipped artifacts (direct fetch, no unzip).
- **Metrics** — every agent comment's `workflow_state` snapshot carries the
  per-step metrics in its `history` entries; on issue close, `summarize-metrics`
  posts an aggregated table.
  Details in [features.md — Metrics](./features.md#metrics--summarization).

---

## Caller authorization (`CALLER_AUTH`)

By default (`CALLER_AUTH: off`) the workflow starts whenever *any* GitHub user
posts an issue or comment whose body matches a trigger (`@@agent` or
`/nebuko`). On a **public** repo that means an outsider can start a full
agent run and burn your LLM tokens.

Set the `CALLER_AUTH` input to `on` to gate the trigger on the author's
repository association:

```yaml
- uses: nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@main
  with:
    CALLER_AUTH: on
```

When `on`, the workflow's `if:` condition requires the triggering author's
`author_association` (from the GitHub event payload) to be one of
`OWNER`, `MEMBER`, or `COLLABORATOR`. Everything else is skipped before any
job, runner time, or token is spent — a denied comment never reaches the LLM.

| `author_association` | `CALLER_AUTH: on` |
|---|---|
| `OWNER`, `MEMBER`, `COLLABORATOR` | ✅ allowed |
| `NONE`, `FIRST_TIMER`, `FIRST_TIME_CONTRIBUTOR`, `MANNEQUIN` | ❌ skipped |
| missing / `null` | ❌ skipped (fail-closed) |

Notes and trade-offs:

- **`workflow_call` is exempt** — the association field is not present on
  `workflow_call` events, so programmatic/Actions triggers are unaffected.
- **`COLLABORATOR` includes read-only invites** — a user invited with
  read-only access can still trigger. If you want "write access or above",
  that needs a permission-API check (out of scope for this gate).
- **On org repos, `MEMBER` = any org member**, not necessarily a repo
  collaborator — slightly broader than strict "repo registered user".
- **Fail-closed by construction** — if `author_association` is ever missing,
  the `contains(...)` check evaluates false and the job simply doesn't start.

**Recommendation: set `CALLER_AUTH: on` for public repos** to prevent
outsider token burn.

---

## Troubleshooting

| Symptom | Checklist |
|---------|-----------|
| No response at all | If using a self-hosted runner (`runs_on: "self-hosted"`), is it online? Are the secrets set? Does the issue body contain a recognized trigger (`@@agent` or `/nebuko`)? Check the run's `if:` condition result in the GHA run. |
| Wrong agent ran | Check the routing cascade ([architecture.md](./architecture.md#routing-cascade)) and the `WORKFLOW_STATE` section of the triggering comment. Use `/nebuko <name>` or `/nebuko force-next-step <step>` to correct course. |
| Stuck in a loop | The circuit breaker (default 15 bot markers) should have stopped it and posted a warning. Review the last few comments, then use `/nebuko resume` with instructions. See [features.md — Safety Mechanisms](./features.md#safety-mechanisms). |
| Agent ran but did nothing useful | Your prompts are too thin — give the agent an explicit role and output contract (step 3). |
| Config errors at startup | The workflow fails fast on undefined `${ref}`s or missing prompt files — read the *Resolve Agent Configuration* step log. |
