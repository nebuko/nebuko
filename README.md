# git-flow-ai

An AI-powered Git workflow automation tool leveraging OpenHands multi-agent architecture. This project automates software development workflows using three specialized AI agents that collaborate on context collection, issue resolution, code implementation, and code review.

## Architecture

The system uses a **three-agent architecture** powered by OpenHands:

- **@@genius (Principal Engineer)**: Responsible for architectural design, logical analysis, and code review. Analyzes issues, delegates context collection, drafts technical plans, and reviews implementations.
- **@@ctx (Context Agent)**: Handles deep context collection across code, issues, PRs, and documentation. Uses a cheap LLM and reports source locations with relevant snippets. Read-only agent — never modifies code.
- **@@dev (Execution Developer)**: Handles code implementation, testing, and validation. Executes technical plans and ensures code quality through automated tests.

## 🧩 Usage in Other Projects

Use this workflow in any GitHub project by copying a single workflow file.

### Quick Start

1. **Copy** `workflow.yaml` from this repo into your project's `.github/workflows/` directory
2. **Add required secrets** to your repository:
   - `OPENROUTER_API_KEY` — your LLM API key (e.g., OpenRouter)
   - `GH_PAT` — a GitHub Personal Access Token with `repo` and `workflow` scopes
3. **Customize** models, temperature, and other settings in the workflow file if desired
4. **Start using** by mentioning `@@genius`, `@@ctx`, or `@@dev` in issues or comments

### Configuration Options

| Input | Default | Description |
|-------|---------|-------------|
| `genius_model` | `openrouter/deepseek/deepseek-v4-pro` | Model for the genius agent |
| `dev_model_low` | `openrouter/deepseek/deepseek-v4-flash` | Model for dev agent (low complexity) |
| `dev_model_medium` | `openrouter/deepseek/deepseek-v4-flash` | Model for dev agent (medium complexity) |
| `dev_model_high` | `openrouter/deepseek/deepseek-v4-flash` | Model for dev agent (high complexity) |
| `ctx_model` | `openrouter/deepseek/deepseek-v4-flash` | Model for the ctx agent |
| `api_base_url` | `https://openrouter.ai/api/v1/chat/completions` | LLM API base URL |
| `genius_temperature` | `0.2` | Temperature for genius agent |
| `dev_temperature` | `0.0` | Temperature for dev agent |
| `llm_seed` | `42` | Seed for reproducible LLM outputs |
| `circuit_breaker_threshold` | `15` | Max bot comments before circuit break |

All complexity and future improvements stay centralized in this repo — your workflow file always references the latest version.

## How It Works

1. An issue or comment is created on GitHub
2. The GitHub Actions workflow (`openhands-multi-agent.yml`) triggers automatically
3. The appropriate agent (@@genius, @@ctx, or @@dev) is invoked based on the mention in the comment
4. The agent processes the request using OpenHands SDK and posts results back to the issue or PR

### Agent Collaboration Flow

```
@@genius (analyzes request)
  → @@ctx (collects context deeply)
  → @@genius (drafts technical plan)
  → @@dev (implements the plan, creates PR)
  → @@genius (reviews PR in PR context)
```

### PR-First Review Process

When @@dev completes implementation:
- A PR is created with `@@genius` tagged in the description to trigger code review
- @@dev does **not** post back to the original issue (this prevents spawning unintended review agents)
- @@genius reviews the PR directly in the PR context (with diffs and changed files)
- Once approved, the original issue author is notified for final human review

## Workflow

The automation pipeline is defined in `.github/workflows/openhands-multi-agent.yml` and handles:
- Issue opened, edited, or assigned events
- Issue comment creation events (including PR comments detected via `github.event.issue.pull_request`)
- Automatic agent selection based on @@genius, @@ctx, or @@dev mentions
- Context injection from GitHub issues and comments
- Agent response posting back to the issue or PR thread (PR comments are routed to the PR, not the issue)