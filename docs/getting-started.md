# Getting Started

Set up git-flow-ai in your repository and run your first multi-agent workflow.
This page assumes you know basic GitHub Actions; for the underlying concepts,
read [architecture.md](./architecture.md) and
[workflows.md](./workflows.md).

---

## Prerequisites

1. **Self-hosted GitHub Actions runner** — the orchestrator runs with
   `runs-on: self-hosted` (agents need a shell with `git`, `uv`, Python, and
   network access to the LLM providers). Setup guide:
   [GitHub's self-hosted runner docs](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners).
2. **Python 3.13** and the **`uv`** package manager on the runner.
3. **GitHub secrets** configured at repo or organization level:

   | Secret | Required | Purpose |
   |--------|----------|---------|
   | `OPENROUTER_API_KEY` | yes | LLM provider token (OpenRouter). |
   | `GH_PAT` | yes | GitHub PAT with `repo` scope — used for git push, issue/PR API calls, and posting comments. |
   | `LOCAL_VLLM_API_TOKEN` | optional | Only if you use a local vLLM provider. |

4. **A configuration file** — `agent-config.yaml` (see step 2 below) and agent
   prompts (step 3), committed to your repository.

---

## Step 1 — Add the Workflow

Copy the consumer file into `.github/workflows/workflow.yaml`:

```yaml
name: AI Dev Workflow (git-flow-ai)

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
    uses: olivier-norture/git-flow-ai/.github/workflows/openhands-multi-agent.yml@main
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
Optional inputs: `summarizer_threshold` (default 100), `python_version`
(default `3.13`), `enable_uv_cache` (default `true`), `prompt_body`.

---

## Step 2 — Create `agent-config.yaml`

Commit a layered config (providers → llms → agents) at the repo root. Minimal
working example with 3 agents:

```yaml
providers:
  openrouter:
    api_url: "https://openrouter.ai/api/v1/chat/completions"
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
- **Run artifacts** — each run uploads its console logs as an artifact.
- **Metrics** — every agent comment embeds an `<!-- agent-metrics: ... -->`
  block; on issue close, `summarize-metrics` posts an aggregated table.
  Details in [features.md — Metrics](./features.md#metrics--summarization).

---

## Troubleshooting

| Symptom | Checklist |
|---------|-----------|
| No response at all | Is the self-hosted runner online? Are the secrets set? Does the issue body contain a recognized trigger (`@@agent` or `/workflow`)? Check the run's `if:` condition result in the GHA run. |
| Wrong agent ran | Check the routing cascade ([architecture.md](./architecture.md#routing-cascade)) and the `WORKFLOW_STATE` section of the triggering comment. Use `/workflow <name>` or `/workflow force-next-step <step>` to correct course. |
| Stuck in a loop | The circuit breaker (default 15 bot markers) should have stopped it and posted a warning. Review the last few comments, then use `/workflow resume` with instructions. See [features.md — Safety Mechanisms](./features.md#safety-mechanisms). |
| Agent ran but did nothing useful | Your prompts are too thin — give the agent an explicit role and output contract (step 3). |
| Config errors at startup | The workflow fails fast on undefined `${ref}`s or missing prompt files — read the *Resolve Agent Configuration* step log. |
