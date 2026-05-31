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
- Automatic agent selection based on @@genius, @@ctx, or @@dev mentions
- Context injection from GitHub issues and comments
- Agent response posting back to the issue thread