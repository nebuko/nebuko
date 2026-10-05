# git-flow-ai

**What it is, in one line:** AI multi-agent workflow as code — a schema and an engine — that lives where *you* are: in GitHub issues and runners. GitLab, Codeberg, BitBucket and Gitea are the direction; today, GitHub is the platform.

Built for **iterative dev** — small iterations, fast delivery, clean code, TDD — not for the "one big prompt builds an app" style. The full story, including the three-layer model and the honest shipped-vs-planned bar, lives in [landscape.md](landscape.md).

## What is the product?

Three layers, and this project owns Layer 2:

| Layer | What it is | Here |
|-------|-----------|------|
| **3 — AI workflow** | A way of doing something with AI: code, review, analyze… | `workflows/` |
| **2 — workflow engine** | The runner; today in GitHub CI/CD | git-flow-ai |
| **1 — AI agent runner** | Runs an individual agent | OpenHands SDK |

The payoff: a new Layer-3 capability — a workflow, an MCP, a websearch or context7 skill — is a **copy into your repo, try it, adopt or delete**. No stack update, no new SaaS, no migration.

And agents are **coworkers, not a black box**: they talk to you and each other in plain issue comments, so you see what they did and why.

## How it works

**Agents with a role and a personality** — defined as code in `agent-config.yaml` (provider, model + parameters, prompt file, skills, tools). Nine agents ship today.

**Skills granted to agents** — markdown procedure files, granted per agent in the same config.

**Workflows as code** — YAML that forces the AI onto a dedicated path, with `human_gate` approval points and conditional routing:

```yaml
- name: plan
  agent: genius
  human_gate: true          # a human must approve before anything is built
  conditions:
    - on_result: approved
      next: stub
    - on_result: rejected
      next: context
```

**Divide and conquer** — small dedicated agents bound long-context drift. When one finishes it posts what it did, found, and the next step, then can dispatch another agent. Every step is a comment you can read and redirect.

**Workflow state in comments** — LLMs are stateless, so every comment embeds a JSON state (current step, history, PR, dispatch). That's what the engine uses to pick the next action — and it's visible, so you know how it works under the hood.

**Choose your LLM** — different models per purpose; complexity-based temperature and per-invocation model override ship today. **Split tasks** — an agent decomposing a huge task into sub-issues (`splitPlan`) ships today.

## Quick start

Runs on GitHub-hosted runners by default; pass `runs_on: "self-hosted"` to use your own [self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners).

1. **Add the reusable workflow** to `.github/workflows/`:

   ```yaml
   jobs:
     agent:
       uses: olivier-norture/git-flow-ai/.github/workflows/openhands-multi-agent.yml@main
       with:
         agent_config_path: "agent-config.yaml"
       secrets:
         OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
         GH_PAT: ${{ secrets.GH_PAT }}
   ```

2. **Add `agent-config.yaml`** at your repo root — see [this repo's config](./agent-config.yaml) for the reference.

3. **Add secrets**: `OPENROUTER_API_KEY` (your LLM key) and `GH_PAT` (a PAT with `repo` and `workflow` scopes).

4. **Start using**: comment `/nebuko default` on an issue, or mention `@@genius`, `@@ctx`, `@@dev` or `@@review` directly.

> 💡 **Zero-copy setup**: workflows, prompts and scripts are fetched from the git-flow-ai repo on every run — you only need `agent-config.yaml` locally. Define custom workflows by adding a local `workflows/` directory (local files take precedence).

## Configuration basics

One layered file, `agent-config.yaml`, in three layers — `[docs/configuration.md](docs/configuration.md)` covers the depth:

| Layer | Purpose | Example |
|-------|---------|---------|
| **Providers** | API endpoint + auth env var | `openrouter` |
| **LLMs** | Model, provider, sampling params | `qwen-3-6-27b` |
| **Agents** | Role → model + prompt file + skills + tools | `genius`, `dev`, `ctx`, … |

Cross-references resolve with `${ref}` (`${llms.qwen-3-6-27b}`). Sampling parameters cascade per-invocation > per-agent > per-LLM, and a `complexity` field auto-adjusts temperature (`low` 0.0 → `high` 0.3, never decreasing the agent's floor). Adding an agent is a ~5-line stanza — no workflow changes needed. If `agent-config.yaml` is absent, the workflow falls back to the legacy env-var approach.

Already paying for a coding subscription? You can point the agents at **CommandCode** (and, later, **OpenCode Go**) instead of a metered API key — see [docs/subscription-plans.md](docs/subscription-plans.md).

For the machine contracts (`AGENT_RUN_CONFIG` sentinel, dispatch statuses, workflow grammar, state schema) see [docs/reference.md](docs/reference.md); for the full agent roster and tool permissions see [docs/agents-and-skills.md](docs/agents-and-skills.md).