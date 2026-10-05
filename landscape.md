# git-flow-ai — Landscape

**What it is, in one line:** AI multi-agent workflow as code — a schema and an engine — that lives where *you* are: in GitHub issues and runners. Today, GitHub is the only supported platform; GitLab, Codeberg, BitBucket, Gitea and friends are the stated direction, not a claim.

---

## Q1 · What is the product?

### Built for iterative dev, not one-prompt apps

git-flow-ai is focused on **iterative software development**: small iterations, fast delivery, clean code, TDD — the practices that make senior developers shine. It is deliberately not the "one big prompt builds an entire app" flow you see all over the internet. That waterfall style loses the best of how humans actually build software: review at design level, short feedback loops, work that lands in small reviewed slices.

### Anti-"AI slop": no forced new tool

A lot of AI products are a good idea packaged in a SaaS that forces you to use *another* tool. But GitHub already has what a developer needs: a place to store code, a place to plan work, and a place to follow the discussion around an issue.

A second drawback of those products: when you want to try a new, cool behaviour, you have to update your whole stack to integrate it. git-flow-ai focuses on **Layer 2**, which changes that:

| Layer | What it is | In this project |
|-------|-----------|-----------------|
| **Layer 3** — AI workflow | A new way of doing something with AI: code, review, analyze… | the workflows in `workflows/` (spec-driven, dev-review-loop, …) |
| **Layer 2** — workflow engine | The runner that executes Layer 3; today in GitHub CI/CD, a dedicated SaaS later | git-flow-ai itself |
| **Layer 1** — AI agent runner | The engine that runs an individual agent | OpenHands SDK |

The payoff: when a new cool tool appears at Layer 3 — a new workflow, an MCP, a websearch or context7 skill — you **copy the flow into your repo**, give it a try on Layer 2, and adopt it or delete it. No stack update, no migration, no new infrastructure. The same story holds for any external capability: it plugs in as a skill or a workflow step, so adoption stays a copy-paste decision.

### Agents as coworkers, not a black box

Agents are introduced as **coworkers**: they talk to you and to the other agents in plain comments on the issue, the same way a human teammate would. You read *what* they did, *what* they found, and *why*. It is no longer a black box — they are real teammates.

---

## Q2 · How does it work?

### Agents with a role and a personality

Every agent is defined **as code** in `agent-config.yaml`: provider, LLM model and sampling parameters, a prompt file carrying its role/personality, the skills it may use, and the tools it may call. Nine agents ship today — among them `genius` (principal engineer), `ctx` (context collector), `dev` (execution developer), `review` (senior code reviewer), `issue` (issue refinement).

### Skills, granted to agents

A skill is a markdown file that tells an agent how to perform a specific procedure (calling another agent, scaffolding a plan, working in stub-only mode, …). Skills are granted per agent from the same config — granting a capability is a one-line diff.

### Workflows as code

A workflow is a declarative YAML document that **forces the AI onto a dedicated path** to do something:

```yaml
name: spec-driven-impl
steps:
  - name: analyze
    agent: genius
    next: context
  - name: plan
    agent: genius
    human_gate: true        # a human must approve before anything is built
    conditions:
      - on_result: approved
        next: stub
      - on_result: rejected
        next: context       # conditional routing based on agent output
  - name: stub
    agent: dev
    skill: [stub-only]      # per-step skill grant
```

`human_gate` keeps design decisions human-owned; conditional output routes the next step from what the agent concluded.

### Divide and conquer

Long context is a known issue: it leads agents to drift out of their rules and lose focus. Small, dedicated agents bound that problem — each one owns a narrow task with a narrow context.

When an agent is done, it posts a comment in the GitHub issue saying what it did, what it found, and what the next step is. It can then call another agent to complete a dedicated task. The result is **auditability**: you know what happened and *why* — every action is a comment you can read, question, and redirect.

### Workflow state: the machine-readable twin of the conversation

Because LLMs are stateless, every agent comment embeds a JSON **workflow state**: where we are in the workflow (which step), the history, whether there is a PR, the dispatch that ends the run, and metrics. That knowledge is what the engine uses to identify the next action and to improve the next run — right-size the context, pick what is important to ingest, detect loops. It is visible in plain comments, so you understand how it works under the hood.

### Choose your LLM

You can use **different models for different purposes** — planning versus coding — and per-agent parameters. Shipped today: complexity-based temperature and a per-invocation model override (an agent can request a specific model for the next run). Direction: automatically escalate to a stronger model when a task is detected to be looping and failing.

### Split the task

Small is beautiful: an agent facing a huge task can decompose it into **sub-issues** (the `splitPlan` mechanism) and have each smaller task completed by its own dedicated run, instead of one oversized agent session.

---

## Shipped vs. planned — the honest bar

| Shipped today | Direction / roadmap |
|---------------|---------------------|
| Agents, skills, workflows as code | Managed SaaS on top of the open core |
| `human_gate` + conditional routing | Multi-platform: GitLab, Codeberg, BitBucket, Gitea |
| JSON workflow state in comments | Auto-escalation to a stronger model on loop failure |
| Split-task sub-issues (`splitPlan`) | GitHub App installation as primary setup |
| Complexity-based temperature, model override | Managed workflow marketplace |

The core is open source (Apache-2.0), self-hosted, and runnable with your own LLM key (a move to AGPL v3 for the core is under discussion — see issue #475). The release philosophy is thin, end-to-end slices, each genuinely working, built in public.

---

## In one paragraph

git-flow-ai is AI multi-agent workflow as code that lives where you already are: in GitHub issues and runners. Agents with real roles collaborate as coworkers — commenting, delegating, and gating work with human approval exactly where it matters — while declarative workflows keep the AI on a defined path. Because the process is code, you copy it, update it with a diff, roll it back with a commit, and measure it; because every step leaves a human-readable comment and a machine-readable state, you always know what happened and why. New AI capabilities are a copy-paste at Layer 3, adopted or deleted without touching your stack.