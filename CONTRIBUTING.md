# Contributing to git-flow-ai

Thank you for your interest in contributing! This document covers how to get started, how to contribute, and how to follow the development workflow.

## 🚀 Getting Started

### Prerequisites

- A GitHub account
- A self-hosted GitHub Actions runner with GPU access (optional, for testing)
- An OpenRouter API key or local vLLM endpoint

### Setup

1. **Fork** this repository
2. **Clone** your fork locally
3. Copy `workflow.yaml` into `.github/workflows/` in your fork
4. Add the required secrets (`OPENROUTER_API_KEY`, `GH_PAT`)
5. Create an issue in your fork and mention `@@genius` to test

## 📋 Development Workflow

We follow a strict git workflow to maintain clean history and clear ownership:

1. **Never push to `main`** — all changes go through feature branches
2. **Branch naming**: `feature/issue-<ID>-<short-name>` (e.g., `feature/issue-125-monetization-foundation`)
3. **Rebase before PR**: `git fetch origin main && git rebase origin/main`
4. **Open a PR** — do not merge yourself
5. **Code review discussions** happen in the issue context, not the PR

## 🤝 How to Contribute

### Reporting Bugs

Create an issue with:
- Clear title and description
- Steps to reproduce
- Expected vs. actual behavior
- Environment details (OS, runner type, models used)

### Suggesting Features

Create an issue with:
- Problem statement
- Proposed solution
- Why it matters (benefits to users)
- Any relevant context or references

### Contributing Code

1. Open or comment on an issue to claim it
2. Create a feature branch from `main`
3. Implement your changes
4. Test thoroughly (run the workflow in your fork)
5. Open a PR with a clear description of changes
6. Address review feedback in the issue

### Contributing Agent Prompts

Agent prompts live in `.agents/prompts/`. To contribute a new or improved prompt:

1. Edit the relevant prompt file (e.g., `.agents/prompts/genius.md`)
2. Test the changes in your fork by running workflows
3. Document what changed and why in your PR description
4. Include before/after examples if possible

## 🧪 Testing

Since this project is a GitHub Actions workflow, testing is done by running it:

1. Copy the workflow and prompts to your fork
2. Create test issues and trigger agents
3. Verify agent behavior and output quality
4. Document results in your PR

## 📝 Code Style

- **Python**: Follow PEP 8, use type hints where practical
- **Markdown**: Use consistent heading hierarchy, keep lines under 100 chars
- **YAML**: Use 2-space indentation, consistent key ordering
- **Comments**: Write meaningful comments; avoid documenting the obvious

## 📄 License

This project is licensed under the Apache License 2.0. By contributing, you agree that your contributions will be licensed under the same terms.

## 🌍 Community Guidelines

- Be respectful and constructive
- Focus on ideas, not individuals
- Help others learn — this is a collaborative project
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md) (if applicable)

## 🔗 Quick Links

- [Issue Tracker](https://github.com/olivier-norture/git-flow-ai/issues)
- [Discussions](https://github.com/olivier-norture/git-flow-ai/discussions)
- [README](README.md)
- [Agent Prompts](.agents/prompts/)
