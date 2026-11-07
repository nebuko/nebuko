---
title: "AGENT_RUN_CONFIG"
audience: contributor
covers:
  - src/nebuko/engine/lib/validate_output.py
  - src/nebuko/engine/agent_host.py
sidebar_group: "Reference"
sidebar_label: "AGENT_RUN_CONFIG contract"
sidebar_order: 11
---

# AGENT_RUN_CONFIG

`AGENT_RUN_CONFIG` is a single-line HTML-comment sentinel that carries a
compact JSON payload. It is a durable, **engine-rendered record** of a run's
hand-off: the engine builds it from typed state (the `dispatch_next` /
`call_agent` control-plane tools) and appends it to the posted comment exactly
once. Agents do **not** emit it themselves (issue #876); the parser below is
retained only as a compatibility input for legacy comments. The canonical
matching pattern is defined in `engine/lib/validate_output.py`; all consumers
(output building, comment preparation) reuse it from there.

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "medium"} -->
```

Single line + HTML comment means no multi-line boundary ambiguity and no leak
into rendered GitHub comments. A sentinel split across lines never matches.

## Canonical Contract (`AgentRunConfig`)

The payload is validated by the canonical model `AgentRunConfig`
(`engine/lib/config/sub_domains/agent_run_config/agent_run_config.py`),
shared by the validator and the routing helpers. Two invariants hold:

1. **At least one action is required** — one of `task`, `dispatch` or
   `function_call`. An empty payload is invalid.
2. **`task` and `function_call` are mutually exclusive** — they are two
   different ways to name a target; combining them is rejected.

`task` is therefore **not** required: a dispatch-only sentinel such as
`{"dispatch": {"status": "advance"}}` is valid. See
[handoff-and-callback.md](../src/nebuko/engine/lib/resolve_config/features/resolve_route/handoff-and-callback.md) for the full contract,
the `callback` return address, and the routing-precedence cascade.

## Dispatch Object

The current dispatch contract (see
`engine/lib/workflow_state/sub_domains/dispatch/dispatch.py`):

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Agent outcome: `advance`, `loop`, `hold`, or `terminate`. The workflow engine resolves routing from this plus the workflow definition. |
| `task_hint` | string | Optional suggested next agent (overrides workflow YAML). |
| `complexity` | string | Complexity for the downstream agent: `low`, `medium`, `high`. When unset, the downstream workflow step's declared `steps[].complexity` applies, else `medium` (issue #787). Auto-adjusts temperature (see [configuration.md](configuration.md)). |
| `reasoning` | string | Reasoning effort: `none`, `low`, `medium`, `xhigh` (`high` is an alias for `xhigh`). Only applies while thinking is enabled in the LLM config. |
| `model` | string | Optional LLM model override for the downstream agent. |
| `temperature` | float | Sampling override. |
| `top_p` | float | Nucleus sampling override. |
| `top_k` | int | Top-K selection override. |
| `min_p` | float | Minimum-P sampling override. |
| `presence_penalty` | float | Presence penalty override. |
| `repetition_penalty` | float | Repetition penalty override. |
| `workflow` | string | Optional workflow name override (file in `workflows/`). |
| `step` | string | Optional step name override within the workflow. |
| `branch` | string | Optional condition key for conditional routing. |
| `parent_workflow` | string | Parent workflow name when entering a sub-workflow. |
| `parent_workflow_step` | string | Parent step that triggered the sub-workflow. |
| `skills` | string | Optional skill(s) for the downstream agent. |
| `pin` | bool | When true, pin this step's output comment(s) so downstream agents receive their full body verbatim (mirrors the `PIN_COMMENT` workflow input / env var). |
| `reason` | string | Optional free-text explanation of the dispatch decision. |

Defaults: `status: "advance"`, `reasoning: "none"`, everything else
empty/null. An unset `complexity` falls back to the workflow step's declared
`steps[].complexity`, else `"medium"` (issue #787).

## Legacy Flat Fields (Backward Compatibility)

Older sentinels use flat fields instead of `dispatch` (see the README schema
table): `task` (the target agent role; required only when neither `dispatch`
nor `function_call` is present), `skills` (string[]),
`complexity`, `reasoning`, `round` (dev↔review callback counter),
`temperature`, `top_p`, `top_k`, `repetition_penalty`, `model`, `workflow`,
`step`, `branch`.

Examples:

```
<!-- AGENT_RUN_CONFIG {"task": "review", "complexity": "medium"} -->
<!-- AGENT_RUN_CONFIG {"task": "dev", "round": 2} -->
<!-- AGENT_RUN_CONFIG {"task": "context", "workflow": "default", "step": "context"} -->
<!-- AGENT_RUN_CONFIG {"task": "dev", "complexity": "low", "top_p": 0.1, "top_k": 10, "repetition_penalty": 1.2} -->
```

**Rule:** human users trigger agents with `@@mention`; only agents may use
`AGENT_RUN_CONFIG`. Bot agents cannot use `@@mention` to avoid self-triggering
loops.
