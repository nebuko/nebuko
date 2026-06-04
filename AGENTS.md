# AGENTS.md — Project Context for AI Agents

This file provides persistent context for AI agents working on this project.
It is loaded automatically for every conversation session.

## Project Overview

**git-flow-ai** is an AI-powered Git workflow automation tool that leverages
OpenHands multi-agent architecture. It automates software development workflows
using five specialized AI agents that collaborate on GitHub issues via a
GitHub Actions workflow.

**Core Value Proposition:** A portable, shareable AI dev team that runs entirely
as a GitHub workflow file. Users create a GitHub issue, mention an agent
(e.g., `@@genius`), and the AI team handles the work — creating branches,
writing code, reviewing, and pinging the user when done.

## Architecture

### Five-Agent System

| Agent | Role | Default Model |
|-------|------|---------------|
| `@@genius` | Principal Engineer — architecture, analysis, planning | `openrouter/deepseek/deepseek-v4-pro` |
| `@@ctx` | Context Agent — deep research, read-only info gathering | `openrouter/deepseek/deepseek-v4-flash` |
| `@@dev` | Execution Developer — code implementation, testing | `openrouter/deepseek/deepseek-v4-flash` |
| `@@review` | Code Reviewer — bug/edge-case detection | `openrouter/deepseek/deepseek-v4-pro` |
| `@@issue` | Issue Refinement — helps humans write better issues | `openrouter/deepseek/deepseek-v4-pro` |

### Key Files

- `workflow.yaml` — The portable workflow definition (copy to any repo)
- `.github/workflows/openhands-multi-agent.yml` — The actual GitHub Actions workflow
- `.agents/prompts/` — Agent prompt definitions (`genius.md`, `ctx.md`, `dev.md`, `review.md`, `issue.md`)
- `.github/scripts/` — Python helper scripts (agent execution, metrics, testing)

### Agent Communication

Agents communicate via GitHub issue comments using structured `agentRunConfig`
JSON blocks:

```json
{"task": "review", "complexity": "medium"}
```

The issue body + comments serve as the shared memory/context store between
agents. This is a deliberate architectural choice — GitHub comments are free,
versioned, searchable, and provide an audit trail.

### Scaling Consideration

Once an issue exceeds ~20 comments, full history injection becomes the primary
scaling bottleneck. **Context summarization** is the top priority improvement
for reducing token costs at scale.

## Monetization Strategy

### Current State

The project is in early-stage foundation. No license existed until recently
(Apache 2.0 added). The product is validated against competitors:

| Competitor | Price | Open-Source | Multi-Agent | GitHub-Native | Self-Hosted |
|---|---|---|---|---|---|
| Copilot Workspace | $19/user/mo | ❌ | ❌ | ✅ | ❌ |
| Devin | Enterprise | ❌ | ❌ | ❌ | ❌ |
| Sweep | Freemium | ❌ | ❌ | ✅ | ❌ |
| DeerFlow | Free | ✅ | ✅ | ❌ | ✅ |
| **git-flow-ai** | **TBD** | ✅ | ✅ | ✅ | ✅ |

**Unique positioning:** The only product with all 5 checkmarks — open-source,
multi-agent, GitHub-native, self-hosted, and pay-per-token.

### Monetization Vectors (Prioritized)

**Phase 0 — Foundation (Now):**
- ✅ Apache 2.0 license
- ✅ CONTRIBUTING.md
- AGENTS.md context documentation

**Phase 1 — Open Core (1–3 months):**
- Free: 5 core agents as they exist today
- Premium: Specialist agents (security auditor, performance optimizer, etc.)
- Premium: Custom agent builder via YAML
- Premium: Usage dashboard and analytics
- **Pay-per-issue pricing:** Users approve a quote before work begins, charged
  when PR opens (not on merge). Pre-paid credits model to reduce friction and
  abuse vectors.

**Phase 1B — Mistral Partnership (1–3 months):**
- EU sovereignty play: "Your code never leaves EU jurisdiction"
- Pre-configured for Mistral Large with EU data residency guarantees
- Targets French administration, EU institutions, healthcare (HDS), defense

**Phase 2 — Managed Cloud (3–6 months):**
- Fully managed git-flow-ai as a GitHub App
- One-click install, pay per task or monthly subscription

**Phase 3 — Agent Marketplace (12+ months):**
- Third-party "Workflow Packs" — specialized agent + workflow definitions
- Revenue share: 70/30 (creator/platform)
- Ecosystem growth lever, not primary revenue driver

### What NOT to Do

1. Don't paywall the core workflow — viral growth = "copy this file"
2. Don't compete on per-token price — compete on workflow value
3. Don't raise VC prematurely — let metrics guide scaling decisions

### Key Metrics to Track

- Tasks completed per repo/week
- Token consumption per agent
- Agent invocation counts
- Average issue-to-PR turnaround time
- User retention and rejection rates

## Git Workflow Rules (Strict)

1. **Never push to `main`**
2. Branch format: `feature/issue-<ID>-<short-name>`
3. Rebase on `origin/main` before creating PR
4. Open PRs, never merge
5. Code review discussions happen in issue context

## Complexity Estimation

| Level | Scope |
|-------|-------|
| LOW | Single file, straightforward change |
| MEDIUM | Multiple files, some integration |
| HIGH | Many files, complex algorithms, cross-component changes |
