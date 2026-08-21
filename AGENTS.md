# Agent Roles & Behavior Reference

Per-agent identity (role, skills, tools) lives in `agent-config.yaml` — the single source of truth. Each prompt file under `.agents/prompts/` contains only the agent's personality and core rules.

Procedural playbooks live in `skills/` and are loaded per agent via its `skills:` list in `agent-config.yaml`:

| Skill | Used by | Contents |
|---|---|---|
| `planning.md` | genius | Phase detection, proposal/plan procedures, sub-issue decomposition, orchestration |
| `context-research.md` | ctx | Search strategies, web search API, complexity-based depth |
| `git-pr-workflow.md` | dev | Rebase checkpoints, branch/PR mechanics |
| `issue-triage.md` | issue | Question format, round structure, state markers, delegation protocol, output templates |
| `debugging.md` | debug | Two-pass log analysis playbook, checkpoint comments |

Shared sections (team context, exit rules, hand-off sentinel format) are injected centrally by the workflow engine — they must NOT be duplicated into prompt files or skills.