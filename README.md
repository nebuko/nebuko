# git-flow-ai

An AI-powered Git workflow automation tool leveraging OpenHands multi-agent architecture. This project automates software development workflows using three specialized AI agents that collaborate on context collection, issue resolution, code implementation, and code review.

## Architecture

The system uses a **three-agent architecture** powered by OpenHands:

- **@@genius (Principal Engineer)**: Responsible for architectural design, logical analysis, and code review. Analyzes issues, delegates context collection, drafts technical plans, and reviews implementations.
- **@@ctx (Context Agent)**: Handles deep context collection across code, issues, PRs, and documentation. Uses a cheap LLM and reports source locations with relevant snippets. Read-only agent — never modifies code.
- **@@dev (Execution Developer)**: Handles code implementation, testing, and validation. Executes technical plans and ensures code quality through automated tests.

## How It Works

1. An issue or comment is created on GitHub
2. The GitHub Actions workflow (`openhands-multi-agent.yml`) triggers automatically
3. The appropriate agent (@@genius, @@ctx, or @@dev) is invoked based on the mention in the comment
4. The agent processes the request using OpenHands SDK and posts results back to the issue

### Agent Collaboration Flow

```
@@genius (analyzes request)
  → @@ctx (collects context deeply)
  → @@genius (drafts technical plan)
  → @@dev (implements the plan)
```

## Workflow

The automation pipeline is defined in `.github/workflows/openhands-multi-agent.yml` and handles:
- Issue opened, edited, or assigned events
- Issue comment creation events
- Pull request opened, edited, or synchronized events
- Automatic agent selection based on @@genius, @@ctx, or @@dev mentions
- Context injection from GitHub issues and comments
- Agent response posting back to the issue thread

## 🧩 Usage in Other Projects

This workflow is **reusable** — any GitHub project can leverage git-flow-ai without forking or copying code.

### Quick Start

1. **Copy the template** — Copy `workflow.yaml` from this repo into your project's `.github/workflows/` directory.

2. **Add required secrets** to your repository:
   - `OPENROUTER_API_KEY` — Your [OpenRouter](https://openrouter.ai/) API key
   - `GH_PAT` — A GitHub Personal Access Token with `repo` scope

3. **Start using** — Mention `@@genius`, `@@ctx`, or `@@dev` in issues or PRs.

### Configuration Options

All inputs have sensible defaults. Override them in your `workflow.yaml` if needed:

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `genius_model` | string | `openrouter/deepseek/deepseek-v4-pro` | Model for @@genius agent |
| `dev_model` | string | `openrouter/qwen/qwen3.6-27b` | Model for @@dev agent |
| `ctx_model` | string | `openrouter/deepseek/deepseek-v4-flash` | Model for @@ctx agent |
| `llm_temperature` | string | `0.2` | LLM temperature (0.0-2.0) |
| `llm_seed` | string | `42` | LLM seed for reproducibility |
| `llm_base_url` | string | OpenRouter URL | Custom LLM API base URL |
| `prompt_path` | string | — | Custom prompt path override |
| `agent_script_url` | string | — | Custom agent script URL |
| `circuit_breaker_threshold` | string | `20` | Max agent calls before circuit breaker |
| `python_version` | string | `3.13` | Python version |
| `enable_uv_cache` | boolean | `true` | Enable uv dependency caching |

### Example with Custom Configuration

```yaml
jobs:
  delegate-to-git-flow-ai:
    uses: olivier-norture/git-flow-ai/.github/workflows/openhands-multi-agent.yml@main
    with:
      genius_model: 'openrouter/anthropic/claude-4-sonnet'
      dev_model: 'openrouter/qwen/qwen3.6-27b'
      llm_temperature: '0.5'
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      GH_PAT: ${{ secrets.GH_PAT }}
```