---
title: "Bot marker conventions"
audience: contributor
sidebar_group: "Reference"
sidebar_label: "Bot markers"
sidebar_order: 16
---

# Bot marker conventions

| Marker | Purpose |
|--------|---------|
| `<!-- bot-workflow-terminal -->` | Universal bot marker prepended (line 1) to every CI-posted comment. Makes human-vs-bot classification deterministic. Defined as `BOT_MARKER` in `github_client.py`. |
| `<!-- bot-agent-{name} -->` | Per-agent marker identifying which agent posted a comment (e.g., `<!-- bot-agent-architect -->`). Used by circuit breaker and metrics extraction. |
| `<!-- bot-agent-script -->` | Pseudo-role marker for deterministic `script` steps (issue #541): the step output is posted under this fixed tag so repeated runs update rather than duplicate, and circuit-breaker marker counting stays intact. |
| `<!-- AGENT_RUN_CONFIG ... -->` | Dispatch sentinel — see [AGENT_RUN_CONFIG](../../../../../../../docs/agent-run-config.md). Canonical regex in `validate_output.py`: `<!--(?:[ \t])*AGENT_RUN_CONFIG[ \t]+(\{.*?\})(?:[ \t])*-->` (single-line, no DOTALL). |
| `<!-- WORKFLOW_STATE_START -->` ... `<!-- WORKFLOW_STATE_END -->` | Collapsed JSON workflow-state section — see [WORKFLOW_STATE v1](../../workflow-state-schema.md). Per-step agent metrics live in its `history` entries. |
| `<!-- po-agent-state: round=N,status=S -->` | Issue-refinement agent state markers (see `AGENTS.md`). |
