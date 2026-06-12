# git-flow-ai

An AI-powered Git workflow automation tool leveraging OpenHands multi-agent architecture. This project automates software development workflows using four specialized AI agents that collaborate on context collection, issue resolution, code implementation, and code review.

## Architecture

The system uses a **five-agent architecture** powered by OpenHands:

- **@@genius (Principal Engineer)**: Responsible for architectural design and logical analysis. Analyzes issues, delegates context collection, and drafts technical plans. Code review is delegated to @@review.
- **@@ctx (Context Agent)**: Handles deep context collection across code, issues, PRs, and documentation. Uses a cheap LLM and reports source locations with relevant snippets. Read-only agent — never modifies code.
- **@@dev (Execution Developer)**: Handles code implementation, testing, and validation. Executes technical plans and ensures code quality through automated tests.
- **@@review (Senior Code Reviewer)**: Inspects code changes, identifies bugs/edge-cases/regressions, and provides thorough code review verdicts. Has terminal access for git diff inspection but cannot modify code.
- **@@issue (Issue Refinement Agent)**: Helps humans create clear, well-structured, and actionable GitHub issues through iterative refinement. Collaborates with the human to clarify requirements, calls @@ctx for context when needed, and produces polished final issues.

## 🧩 Usage in Other Projects

Use this workflow in any GitHub project by copying a single workflow file.

### Quick Start

1. **Copy** `workflow.yaml` from this repo into your project's `.github/workflows/` directory
2. **Add required secrets** to your repository:
   - `OPENROUTER_API_KEY` — your LLM API key (e.g., OpenRouter)
   - `GH_PAT` — a GitHub Personal Access Token with `repo` and `workflow` scopes
3. **Customize** models, temperature, and other settings in the workflow file if desired
4. **Start using** by mentioning `@@genius`, `@@ctx`, `@@dev`, `@@review`, or `@@issue` in issues or comments

### Configuration Options

The system uses a **layered YAML config** (`agent-config.yaml`) instead of flat workflow inputs. This eliminates 25+ duplicated parameters and makes configuration agent-writable and human-readable.

#### Three-Layer Structure

| Layer | Purpose | Example |
|-------|---------|---------|
| **Providers** | API endpoint + auth token env var | `openrouter`, `local_vllm` |
| **LLMs** | Model path, provider, temperature, thinking params, sampling params | `deepseek-v4-pro`, `qwen-3-6-30b` |
| **Agents** | Role → model + prompt file + skills + per-agent sampling overrides | `genius`, `dev`, `ctx`, `review`, `issue` |

#### LLM Sampling Parameters

Each LLM (and optionally each agent) can define sampling parameters to control instruction following:

| Parameter | Type | Description | Typical Range |
|-----------|------|-------------|---------------|
| `top_p` | float | Nucleus sampling — lower values produce more deterministic output | 0.1–1.0 |
| `top_k` | int | Top-K token selection — limits candidate pool | 10–100 |
| `min_p` | float | Minimum P sampling — filters tokens below this absolute probability threshold | 0.0–1.0 |
| `presence_penalty` | float | Presence penalty — discourages repeating tokens based on presence | -2.0–2.0 |
| `repetition_penalty` | float | Repetition penalty — values > 1.0 discourage repetition | 1.0–1.5 |

These parameters cascade: per-invocation (`agentRunConfig`) > per-agent (YAML `agents.`) > per-LLM (YAML `llms.`). Lower `top_p` (0.1–0.5) combined with low temperature (0.0) dramatically improves instruction adherence.

#### Dynamic Complexity-Based Temperature

When `complexity` is set in `agentRunConfig` and no explicit `temperature` override is provided, temperature is auto-adjusted:
- `low` → 0.0 (most deterministic)
- `medium` → 0.1
- `high` → 0.3 (more creative)

The agent's base temperature from `agent-config.yaml` serves as a floor — complexity only increases temperature, never decreases it.

#### `${ref}` Syntax

References resolve cross-layer: `${providers.openrouter}` → provider dict, `${llms.deepseek-v4-pro}` → LLM config dict.

#### Minimal Workflow Usage

```yaml
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

Adding an agent is a 5-line stanza in `agent-config.yaml` — no workflow changes needed.

**Backward compatibility**: If `agent-config.yaml` is absent, the workflow falls back to the old env-var-based approach. Existing downstream projects continue to work without modification.

## How It Works

1. An issue or comment is created on GitHub
2. The GitHub Actions workflow (`openhands-multi-agent.yml`) triggers automatically
3. The appropriate agent (@@genius, @@ctx, @@dev, @@review, or @@issue) is invoked based on the `agentRunConfig` JSON block or `@@mention` in the comment
4. The agent processes the request using OpenHands SDK and posts results back to the issue

### Agent Invocation

Agents communicate using a structured JSON format called `agentRunConfig`. This replaces the old `@@mention`-based handoff with a typed, extensible configuration:

```
<agentRunConfig>
{"task": "review", "complexity": "medium"}
</agentRunConfig>
```

#### agentRunConfig Schema

| Parameter | Type | Required | Description | Default |
|-----------|------|----------|-------------|---------|
| `task` | string | **yes** | Agent role: `genius`, `ctx`, `dev`, `review`, `issue` | — |
| `skills` | string[] | no | Skills to attach (reserved for future use) | `[]` |
| `complexity` | string | no | Task complexity: `low`, `medium`, `high`. Auto-adjusts temperature. | `"medium"` |
| `reasoning` | string | no | Reasoning effort: `none`, `low`, `medium`, `high` | `"none"` |
| `round` | int | no | Callback counter for dev↔review cycles | — |
| `temperature` | float | no | Override default temperature | — |
| `top_p` | float | no | Nucleus sampling (lower = more deterministic) | from LLM config |
| `top_k` | int | no | Top-K token selection | from LLM config |
| `repetition_penalty` | float | no | Repetition penalty (>1.0 reduces repetition) | from LLM config |
| `model` | string | no | Override the LLM model for this invocation | — |
| `workflow` | string | no | Workflow name (file in `workflows/`) | — |
| `step` | string | no | Current step name within workflow | first step |
| `branch` | string | no | Condition key for conditional routing | — |

#### Example Invocations

**Dev calls review:**
```json
{"task": "review", "complexity": "medium"}
```

**Review sends back to dev (re-review):**
```json
{"task": "dev", "round": 2}
```

**Genius plans a high-complexity task:**
```json
{"task": "dev", "complexity": "high", "reasoning": "high"}
```

**Genius demands strict instruction following:**
```json
{"task": "dev", "complexity": "low", "top_p": 0.1, "top_k": 10, "repetition_penalty": 1.2}
```

**Bot handoff with workflow context:**
```json
{"task": "ctx", "workflow": "default", "step": "context"}
```

### Workflows

Workflows are first-class YAML definitions that orchestrate sequences of agents. They live in the `workflows/` directory at the repo root.

#### Defining a Workflow

```yaml
# workflows/default.yaml
name: default
description: "Standard dev workflow: analyze → context → plan → implement → review"
trigger:
  agents: [genius]  # which @@mentions can start this workflow
steps:
  - name: analyze
    agent: genius
    next: context

  - name: context
    agent: ctx
    next: plan

  - name: plan
    agent: genius
    next: implement

  - name: implement
    agent: dev
    next: review

  - name: review
    agent: review
    next: null  # terminal step
```

#### Conditional Branching

Workflows support conditional routing based on agent output:

```yaml
steps:
  - name: implement
    agent: dev
    conditions:
      - on_result: "needs_review"
        next: review
      - on_result: "needs_revision"
        next: implement  # loop back
      - on_result: "default"
        next: null
```

#### Built-in Workflows

| Workflow | Description | Trigger |
|----------|-------------|---------|
| `default` | Full dev cycle: analyze → context → plan → implement → review | `@@genius` |
| `review-only` | Standalone code review | `@@review` |

#### Workflow Selection

- **Without `workflow` field**: Direct task routing (backward compatible)
- **With `workflow` field**: Resolves agent from workflow step definition
- **With `workflow` + `step`**: Jumps to specific step within workflow
- **With `branch`**: Triggers conditional routing based on condition key

#### Human Invocation (Backward Compatible)

Human users can still use `@@mention` shorthand (`@@genius`, `@@ctx`, `@@dev`, `@@review`, `@@issue`). The `agentRunConfig` format is primarily for agent→agent communication. The workflow supports both formats with the `@@mention` fallback reserved for human triggers only — bot agents cannot use `@@mention` to avoid self-triggering loops.

### Agent Collaboration Flow

```
@@genius (analyzes request)
  → @@ctx (collects context deeply)
  → @@genius (presents architecture proposal)
  → human (approves or requests changes)
  → @@genius (drafts implementation plan)
  → @@dev (implements the plan)
  → @@review (reviews the code)

@@issue (refines issue with human)
  → @@ctx (collects context when needed)
  → @@issue (incorporates context and refines)
  → human (iterates until satisfied)
```

## Workflow

The automation pipeline is defined in `.github/workflows/openhands-multi-agent.yml` and handles:
- Issue opened, edited, or assigned events
- Issue comment creation events
- Automatic agent selection based on @@genius, @@ctx, @@dev, or @@review mentions
- Context injection from GitHub issues and comments
- Agent response posting back to the issue thread