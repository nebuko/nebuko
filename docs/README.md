# Documentation

nebuko is a **multi-agent AI orchestration system** built on GitHub
Actions + the OpenHands SDK. Five specialized agents (architect, context
collector, developer, reviewer, product owner) collaborate inside your GitHub
issues — posting comments, persisting state, and handing off work through
declarative YAML workflows — to turn issues into reviewed pull requests.

For the high-level pitch and quick overview, see the root
[README.md](../README.md).

---

## Reading Guide

| Document | Audience | Description |
|----------|----------|-------------|
| [adoption.md](./adoption.md) | New users | Adopt Nebuko in your repo: intro, entrypoint contract, GitHub App authentication (PAT fallback), provider configs, runner choice. |
| [getting-started.md](./getting-started.md) | New users | Prerequisites, setup, first workflow run, troubleshooting. |
| [architecture.md](./architecture.md) | Everyone | System overview: agents, orchestration loop, state model, routing cascade. |
| [workflows.md](./workflows.md) | Users & contributors | Declarative workflow engine + all built-in workflows with step graphs. |
| [root-cause-workflow.md](./root-cause-workflow.md) | Users & contributors | The `root-cause` workflow: investigate, engine-run per-delta oracle, confirm, seal, hand off. |
| [lint.md](./lint.md) | Contributors | Offline graph-level linter for workflow YAML: rules L1–L11, CLI usage, exit codes, CI integration. |
| [replay.md](./replay.md) | Contributors | Deterministic, no-LLM replay of per-invocation routing and complexity decisions from a workflow state (issue or state file). |
| [agents-and-skills.md](./agents-and-skills.md) | Users & contributors | The 5 agents, the 13 skills, and the tool permission model. |
| [configuration.md](./configuration.md) | Operators | Layered `agent-config.yaml`: providers → llms → agents, `${ref}` resolution. |
| [subscription-plans.md](./subscription-plans.md) | Operators | Run agents on a subscription plan (CommandCode today; OpenCode Go planned). |
| [safety-mechanisms.md](./safety-mechanisms.md) | Operators | Loop guard and circuit breaker. |
| [remote-workflows.md](./remote-workflows.md) | Operators | Zero-copy remote workflows via `workflow_call`. |
| [comment-recall.md](./comment-recall.md) | Contributors | The `fetch_comment_bodies` tool. |
| [agent-run-config.md](./agent-run-config.md) | Contributors | The `AGENT_RUN_CONFIG` dispatch sentinel. |
| [external-references.md](./external-references.md) | Contributors | How `@@context` shares external references (and the JSON schema). |
| [environment-variables.md](./environment-variables.md) | Operators | Environment variable catalog. |
| [step-hooks.md](./step-hooks.md) | Users | `pre_hook` / `post_hook` step hooks. |
| [typed-inputs-and-outputs.md](./typed-inputs-and-outputs.md) | Users | Typed workflow inputs and step outputs. |
| [built-in-workflows.md](./built-in-workflows.md) | Users | The shipped workflows and their step graphs. |
| [handoff-and-callback.md](../src/nebuko/engine/lib/resolve_config/features/resolve_route/handoff-and-callback.md) | Contributors | The `AGENT_RUN_CONFIG` contract: dispatch vs function_call, the callback return address, routing precedence, the no-action default. |
| [architecture_code.md](./architecture_code.md) | Contributors | Internal DDD package-by-feature layout of the Python code. |
| [github-api-limits.md](./github-api-limits.md) | Operators | GitHub REST rate limits relevant to the comment-based state machine. |
| [adr/0005-github-api-budget.md](./adr/0005-github-api-budget.md) | Operators | How many workflow runs/hour the default 7-agent flow affords, and the GitHub API guard policy. |
| [adr/0012-complexity-governance.md](./adr/0012-complexity-governance.md) | Operators | Layered per-repo `ComplexityPolicy`, evidence bundles, and the `ComplexityGovernor` tier adjudicator. |
| [adr/0013-step-outputs-are-step-comments.md](./adr/0013-step-outputs-are-step-comments.md) | Contributors | A step's output is its posted comment: step-name references, `{{ <step> }}` prompt syntax, locator storage, no truncation. |
| [adr/0014-cache-aware-cost-and-default-price-service.md](./adr/0014-cache-aware-cost-and-default-price-service.md) | Contributors | Cost precedence chain, four-rate cache-aware session cost, the OpenRouter-backed default price service, and the removal of SDK price forwarding. |

Business documents (not user documentation): `etude-de-marche.md` (market
study, in French), and the root `landscape.md` (product positioning — "what
is the product" + "how does it work"). Agent behavior contract: root
[`AGENTS.md`](../AGENTS.md).

---

## Feature docs (colocated with the code)

A doc that describes one feature lives in that feature's folder, next to the
code it documents, so a change to the code is a change in the same directory.
`docs/` keeps only cross-cutting pages and ADRs.

| Document | Audience | Description |
|----------|----------|-------------|
| [human-commands.md](../src/nebuko/engine/lib/resolve_config/sub_domains/trigger_commands/human-commands.md) | Users | `@@agent`, `/nebuko ...` and the human command surface. |
| [metrics.md](../src/nebuko/engine/lib/agent_script/features/collect_metrics/metrics.md) | Contributors | Metrics collected per run and their schema. |
| [execution-layer.md](../src/nebuko/engine/lib/agent_script/features/run_agent/execution-layer.md) | Contributors | How an agent run executes (OpenHands, watchdog, publish). |
| [workflow-yaml-grammar.md](../src/nebuko/engine/lib/config/sub_domains/workflow_definition/workflow-yaml-grammar.md) | Users | Key-by-key workflow YAML grammar. |
| [workflow-state-schema.md](../src/nebuko/engine/lib/workflow_state/workflow-state-schema.md) | Contributors | The `WORKFLOW_STATE` v1 JSON schema. |
| [bot-markers.md](../src/nebuko/engine/lib/workflow_state/features/classify_comment/bot-markers.md) | Contributors | Bot marker conventions. |
| [step-instructions.md](../src/nebuko/engine/lib/config/sub_domains/workflow_step/step-instructions.md) | Users | The step `prompt` field and skill injection. |
| [script-steps.md](../src/nebuko/engine/lib/config/sub_domains/workflow_step/script-steps.md) | Users | Script steps (`command:`). |
| [per-step-complexity.md](../src/nebuko/engine/lib/complexity_governor/per-step-complexity.md) | Users | Per-step complexity tiers. |
| [human-gates.md](../src/nebuko/engine/lib/resolve_config/features/resolve_route/human-gates.md) | Users | Human gates and releasing them. |
| [idempotency.md](../src/nebuko/engine/lib/git/idempotency.md) | Contributors | Why committed work is never re-applied on a re-run. |
| [branch-base-sync-policy.md](../src/nebuko/engine/lib/git/branch-base-sync-policy.md) | Contributors | Merge the base only on conflict, never at publish time. |
| [git-flow.md](../src/nebuko/engine/lib/agent_script/features/ensure_pr/git-flow.md) | Contributors | Agents commit, the engine pushes and opens the PR. |
| [publication.md](../src/nebuko/engine/lib/agent_script/features/ensure_pr/publication.md) | Contributors | How agent work becomes a PR. |
| [finalize-salvage.md](../src/nebuko/engine/lib/agent_script/features/finalize_tool/finalize-salvage.md) | Contributors | The `finalize` report and the salvage fallback. |
| [session-handoff.md](../src/nebuko/engine/lib/agent_script/features/handoff_tool/session-handoff.md) | Contributors | The `handoff` tool and resuming an interrupted run. |
| [agent-answer-triggering.md](../src/nebuko/engine/lib/resolve_config/features/parse_trigger/agent-answer-triggering.md) | Contributors | How a bot agent answer re-enters the engine. |
| [adapters.md](../src/nebuko/adapters/adapters.md) | Contributors | Platform adapters. |
| [github-app-auth.md](../src/nebuko/adapters/github-app-auth.md) | Operators | GitHub App token behaviour and the agent-start delay. |
| [nebuko-init.md](../src/nebuko/engine/init/nebuko-init.md) | Operators | App-aware `nebuko init` setup. |
| [testing-e2e.md](../tests/engine/e2e/testing-e2e.md) | Contributors | End-to-end test harness. |

---

## Suggested Paths

- **New user:** getting-started → architecture → workflows
- **Contributor:** architecture → architecture_code → features
- **Operator:** configuration → subscription-plans → reference

All documents cross-reference each other; any of them can be a valid entry
point.
