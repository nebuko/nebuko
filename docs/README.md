# Documentation

git-flow-ai is a **multi-agent AI orchestration system** built on GitHub
Actions + the OpenHands SDK. Nine specialized agents (planner, context
collector, developer, reviewer, and more) collaborate inside your GitHub
issues — posting comments, persisting state, and handing off work through
declarative YAML workflows — to turn issues into reviewed pull requests.

For the high-level pitch and quick overview, see the root
[README.md](../README.md).

---

## Reading Guide

| Document | Audience | Description |
|----------|----------|-------------|
| [getting-started.md](./getting-started.md) | New users | Prerequisites, setup, first workflow run, troubleshooting. |
| [architecture.md](./architecture.md) | Everyone | System overview: agents, orchestration loop, state model, routing cascade. |
| [workflows.md](./workflows.md) | Users & contributors | Declarative workflow engine + all built-in workflows with step graphs. |
| [agents-and-skills.md](./agents-and-skills.md) | Users & contributors | The 9 agents, the 5 skills, and the tool permission model. |
| [configuration.md](./configuration.md) | Operators | Layered `agent-config.yaml`: providers → llms → agents, `${ref}` resolution. |
| [features.md](./features.md) | Contributors | Deep dives: human commands, task decomposition, safety, metrics, execution layer, zero-copy. |
| [reference.md](./reference.md) | Contributors | Machine contracts: dispatch sentinel, workflow grammar, state schema, env vars, markers. |
| [default-workflow.md](./default-workflow.md) | Operators | Step-by-step reference of the GitHub Actions orchestrator (jobs, steps, data flow). |
| [architecture_code.md](./architecture_code.md) | Contributors | Internal DDD package-by-feature layout of the Python code. |
| [github-api-limits.md](./github-api-limits.md) | Operators | GitHub REST rate limits relevant to the comment-based state machine. |

Business documents (not user documentation): `etude-de-marche.md` (market
study, in French), and the root `landscape.md` (product positioning — "what
is the product" + "how does it work"). Agent behavior contract: root
[`AGENTS.md`](../AGENTS.md).

---

## Suggested Paths

- **New user:** getting-started → architecture → workflows
- **Contributor:** architecture → architecture_code → features
- **Operator:** configuration → reference → default-workflow

All documents cross-reference each other; any of them can be a valid entry
point.
