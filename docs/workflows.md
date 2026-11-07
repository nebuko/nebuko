---
sidebar_group: "Guides"
sidebar_label: "Write workflows"
sidebar_order: 10
---

# Workflows

Declarative multi-step agent chains. A workflow is a YAML file in `.nebuko/workflows/` (or shipped under `defaults/workflows/`)
that names an ordered set of steps; each step runs one agent (or mounts a
sub-workflow), and routing between steps is driven by `next` and `conditions`.
The full key-by-key grammar lives in
[Workflow YAML Grammar](../src/nebuko/engine/lib/config/sub_domains/workflow_definition/workflow-yaml-grammar.md).

---

## How Declarative Workflows Work

A workflow defines **who runs, in what order, and what can make the chain
branch or stop**:

```yaml
name: my-workflow
trigger:
  agents: [architect]            # @@architect in an issue body can start it
steps:
  - name: analyze
    agent: architect
    next: plan
  - name: plan
    agent: architect
    conditions:               # evaluated in order
      - on_result: approved
        next: implement
      - on_result: rejected
        next: analyze         # loop back
  - name: implement
    agent: dev
    next: null                # terminal
```

- Steps execute sequentially; `next: null` ends the workflow.
- `conditions[]` give the agent a choice of where to go, based on the result
  it reports in its dispatch (`status` / `task_hint`). Always include a
  catch-all (e.g., `default`) so routing stays deterministic.
- `skill: [...]` attaches extra prompt context from `defaults/skills/` to that step; each entry is a bare
  name (means `inject`) or `- inject: [names]` / `- discover: [names]` (issue #955).
- `prompt:` injects a standing instruction into that step's agent context
  (see [Step Instructions](#step-instructions-prompt)).
- `complexity: low|medium|high` declares the complexity tier for that step's
  agent invocation (see [Per-step Complexity](#per-step-complexity)).
- `prompt_replace: true` makes the step `prompt:` *replace* the role prompt
  entirely, so the role's generic flow cannot redefine the step's scope
  (see [Step Instructions](#step-instructions-prompt)).
- `human_gate: true` marks a step as a **security boundary**: the workflow is
  held after that step until a human releases it (see [Human Gates](#human-gates)).
- Workflows are started by GitHub CI (an issue comment/opened event) or by a
  `/nebuko run workflow --name <name>` command, which enters at the workflow's
  **first step**. A `dispatch` field pointing at a workflow also routes to it.
  Human commands override state-based routing — see the routing cascade in
  [architecture.md](./architecture.md#routing-cascade).

---

## Writing a Custom Workflow

Create `workflows/<name>.yaml`:

```yaml
name: my-workflow
description: "What this workflow does"
trigger:
  agents: [architect]
steps:
  - name: step_one
    agent: architect
    next: step_two
  - name: step_two
    agent: dev
    skill: [python]
    next: null
```

Rules:

- `name` must match the filename (without `.yaml`).
- Start by typing `@@<trigger agent>` in an issue, or use
  `/nebuko my-workflow` to force it on an existing issue.
- Local files take precedence over remotely fetched workflows of the same name
  (zero-copy fetch — see [remote-workflows.md](remote-workflows.md)).

**Reusable sub-workflows** are just workflows referenced by `workflow:` in a
step — `dev-review-loop` and `review-only` are the built-in examples. The
referenced path may be a workflow name (resolved against `workflows/`) or a
relative file path (e.g., `tmp_impl/workflow.yaml`), which enables
runtime-generated workflows.

**Nested sub-workflows**: a step that references a workflow whose first step is
itself a `workflow:` reference is supported (e.g. `tmp_impl` → `dev-review-loop`).
On entry, the resolver descends to the innermost runnable leaf
(`agent:` or `script:` step), up to a depth guard with cycle detection. `skill:`
values declared on the referencing parent step propagate down to the leaf.
Each sub-workflow's terminal return resumes only its *immediate* parent; an
outer parent pair already on record survives nested re-entry, so the chain
unwinds one level per return.

**Position (the nested call-stack).** The engine tracks where a run is as a
**position** — the ordered stack of `(workflow, step)` frames from the
outermost workflow down to the step actually executing (persisted as
`WorkflowState.position`). It is carried forward across transitions and
mutated by exactly one frame per move: entry appends a frame, an
intra-workflow step move replaces the leaf, and return-to-parent pops it.
This is what keeps a `plan-orchestration → tmp_impl → dev-review-loop` run
inside `tmp_impl/step_1` while `implement → review` happens, and what lets the
terminal `review` resume `tmp_impl/step_2` instead of collapsing to the outer
`sub_clean`. See
[ADR-0006](./adr/0006-nested-workflow-position-carry-forward.md).
