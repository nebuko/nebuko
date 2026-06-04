# git-flow-ai

An AI-powered Git workflow automation tool leveraging OpenHands multi-agent architecture. This project automates software development workflows using four specialized AI agents that collaborate on context collection, issue resolution, code implementation, and code review.

## Architecture

The system uses a **four-agent architecture** powered by OpenHands:

- **@@genius (Principal Engineer)**: Responsible for architectural design and logical analysis. Analyzes issues, delegates context collection, and drafts technical plans. Code review is delegated to @@review.
- **@@ctx (Context Agent)**: Handles deep context collection across code, issues, PRs, and documentation. Uses a cheap LLM and reports source locations with relevant snippets. Read-only agent — never modifies code.
- **@@dev (Execution Developer)**: Handles code implementation, testing, and validation. Executes technical plans and ensures code quality through automated tests.
- **@@review (Senior Code Reviewer)**: Inspects code changes, identifies bugs/edge-cases/regressions, and provides thorough code review verdicts. Has terminal access for git diff inspection but cannot modify code.

## 🧩 Usage in Other Projects

Use this workflow in any GitHub project by copying a single workflow file.

### Quick Start

1. **Copy** `workflow.yaml` from this repo into your project's `.github/workflows/` directory
2. **Add required secrets** to your repository:
   - `OPENROUTER_API_KEY` — your LLM API key (e.g., OpenRouter)
   - `GH_PAT` — a GitHub Personal Access Token with `repo` and `workflow` scopes
3. **Customize** models, temperature, and other settings in the workflow file if desired
4. **Start using** by mentioning `@@genius`, `@@ctx`, `@@dev`, or `@@review` in issues or comments

### Configuration Options

| Input | Default | Description |
|-------|---------|-------------|
| `genius_model` | `openrouter/deepseek/deepseek-v4-pro` | Model for the genius agent |
| `dev_model_low` | `openrouter/deepseek/deepseek-v4-flash` | Model for dev agent (low complexity) |
| `dev_model_medium` | `openrouter/deepseek/deepseek-v4-flash` | Model for dev agent (medium complexity) |
| `dev_model_high` | `openrouter/deepseek/deepseek-v4-flash` | Model for dev agent (high complexity) |
| `ctx_model` | `openrouter/deepseek/deepseek-v4-flash` | Model for the ctx agent |
| `review_model` | `openrouter/deepseek/deepseek-v4-pro` | Model for the review agent |
| `api_base_url` | `https://openrouter.ai/api/v1/chat/completions` | LLM API base URL |
| `genius_base_url` | *(empty)* | Base URL override for genius model |
| `dev_model_low_base_url` | *(empty)* | Base URL override for dev low-complexity model |
| `dev_model_medium_base_url` | *(empty)* | Base URL override for dev medium-complexity model |
| `dev_model_high_base_url` | *(empty)* | Base URL override for dev high-complexity model |
| `ctx_base_url` | *(empty)* | Base URL override for ctx model |
| `genius_api_key` | *(empty)* | API key override for genius model (falls back to `OPENROUTER_API_KEY`) |
| `dev_model_low_api_key` | *(empty)* | API key override for dev low-complexity model |
| `dev_model_medium_api_key` | *(empty)* | API key override for dev medium-complexity model |
| `dev_model_high_api_key` | *(empty)* | API key override for dev high-complexity model |
| `ctx_api_key` | *(empty)* | API key override for ctx model |
| `review_api_key` | *(empty)* | API key override for review model |
| `genius_temperature` | `0.2` | Temperature for genius agent |
| `dev_temperature` | `0.0` | Temperature for dev agent |
| `llm_seed` | `42` | Seed for reproducible LLM outputs |
| `circuit_breaker_threshold` | `15` | Max bot comments before circuit break |

All complexity and future improvements stay centralized in this repo — your workflow file always references the latest version.

## How It Works

1. An issue or comment is created on GitHub
2. The GitHub Actions workflow (`openhands-multi-agent.yml`) triggers automatically
3. The appropriate agent (@@genius, @@ctx, @@dev, or @@review) is invoked based on the mention in the comment
4. The agent processes the request using OpenHands SDK and posts results back to the issue

### Agent Collaboration Flow

```
@@genius (analyzes request)
  → @@ctx (collects context deeply)
  → @@genius (drafts technical plan)
  → @@dev (implements the plan)
  → @@review (reviews the code)
```

## Workflow

The automation pipeline is defined in `.github/workflows/openhands-multi-agent.yml` and handles:
- Issue opened, edited, or assigned events
- Issue comment creation events
- Automatic agent selection based on @@genius, @@ctx, @@dev, or @@review mentions
- Context injection from GitHub issues and comments
- Agent response posting back to the issue thread