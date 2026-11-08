---
title: Prompt plan
audience: contributor
sidebar_group: "Concepts"
sidebar_label: "How the agent prompt is assembled"
sidebar_order: 40
---

# Prompt plan: what goes into the agent prompt, and in which order

The engine builds the agent prompt in two steps:

1. **Plan** — `Engine.plan_prompt(...)` decides *what* the prompt contains and in
   *which order*. It returns a `PromptPlan` (`prompt_plan.py`): an ordered list of
   `PromptSection`s, pure data.
2. **Render** — `render_prompt(plan)` is the context builder. It joins the
   included sections with a blank line. `Engine.build_prompt` is just
   `render_prompt(plan_prompt(...))`.

Because rendering is a plain join, the rendered prompt is byte-identical to what
the previous hand-concatenated `build_prompt` produced.

## Sections

In prompt order. A section that was considered but contributed nothing stays in
the plan with empty content (and a `reason` when known), so you can see *why* a
source is absent.

| kind | source | notes |
|---|---|---|
| `scaffold_conflict` | branch scaffold | merge-conflict note; always first |
| `role_prompt` | `PROMPT_LOCATION` | dropped (`reason: dropped by prompt_replace`) when the step replaces it |
| `step_prompt` | `STEP_PROMPT_FILE` | `{{ step }}` refs already substituted |
| `output_contract` | step `output:` | declared section markers |
| `injected_skills` | `inject`-mode skills | full bodies |
| `inputs` | step `inputs:` | one part per materialized input |
| `handoff` | session handoff file | claims to re-check |
| `request_header` | — | `--- USER REQUEST / GITHUB CONTEXT ---` |
| `trigger` | the trigger | author, issue body, comment body |
| `task` | `TASK` | |
| `engine_status` | previous state `engine_failure` | |
| `gate_decision` | `GATE_DECISION` | |
| `thread` | context selection | `parts` list each selected entry (kind, comment id, heading) |

## Adding something to the prompt

Add a `PromptSection` in `plan_prompt` (or `_context_sections` for the
user-request half) with a `kind`, a `source`, and its text. Do not concatenate
strings into the prompt elsewhere: anything outside the plan is invisible to
`replay --context`.

## Inspecting a plan

`python -m nebuko.engine.lib.replay --state state.json --entry N --context`
shows the plan, the rendered prompt and the output the live run recorded; see
[Replaying engine decisions](../../../docs/replay.md).
