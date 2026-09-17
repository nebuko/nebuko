# Workflows

Declarative multi-step agent chains. A workflow is a YAML file in `workflows/`
that names an ordered set of steps; each step runs one agent (or mounts a
sub-workflow), and routing between steps is driven by `next` and `conditions`.
The full key-by-key grammar lives in
[reference.md вАФ Workflow YAML Grammar](./reference.md#workflow-yaml-grammar).

---

## How Declarative Workflows Work

A workflow defines **who runs, in what order, and what can make the chain
branch or stop**:

```yaml
name: my-workflow
trigger:
  agents: [genius]            # @@genius in an issue body can start it
steps:
  - name: analyze
    agent: genius
    next: plan
  - name: plan
    agent: genius
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
- `skill: [...]` attaches extra prompt context from `skills/` to that step.
- `prompt:` injects a standing instruction into that step's agent context
  (see [Step Instructions](#step-instructions-prompt)).
- `human_gate: true` marks a step as a **security boundary**: the workflow is
  held after that step until a human releases it (see [Human Gates](#human-gates)).
- Workflows can be started by an `@@mention` (matching `trigger.agents`), a
  `/workflow <name>` command, or a `dispatch` field pointing at them. Human
  commands override state-based routing вАФ see the routing cascade in
  [architecture.md](./architecture.md#routing-cascade).

---

## Step Instructions (`prompt`)

A step can carry a `prompt:` field with a standing instruction that is
injected into that step's agent context every time the step runs вАФ useful for
repetitive per-workflow directives such as output-format contracts or analysis
requirements.

```yaml
steps:
  - name: analyze
    agent: genius
    prompt: |
      Analyze the transcription and provide me:
      a short summary (~5-10 sentences)
      5 Key points
      A possible link/impact/benefits to the https://github.com/your/repo project
    next: context
```

**Semantics & precedence.** The step prompt is a *standing instruction* for
that step вАФ it governs the format and behavior of that step. The issue body
remains the *task-specific* content and wins on task specifics; the role prompt
stays the base identity. Injection order in the agent context:
**role identity вЖТ step instruction вЖТ issue body/history** (section header:
`### рЯФі STEP INSTRUCTION (from workflow.yaml)`).

**Mechanics.** The resolver writes the prompt verbatim to
`/tmp/step_prompt.md` and emits `STEP_PROMPT_FILE` (path only вАФ the prompt may
contain newlines). When the step has no `prompt`, the variable is empty and any
stale file is removed, so existing workflows are unaffected. Multi-line YAML
block scalars (`|` / `>`) are supported.

**`prompt:` vs `skill:`.** Use `prompt:` for short, self-contained,
per-step directives that should live inline in the workflow. Use
`skill: [...]` for richer, multi-file, or reusable knowledge files in
`skills/`. They compose: a step may have both.

---

## Human Gates

`human_gate: true` is a security boundary enforced by the engine вАФ not a
routing choice made by the agent. It guarantees that **no LLM can advance the
workflow past a gated step on its own**; only a human action releases it.

Mechanics:

1. The gated step's agent finishes and emits `advance`. The engine intercepts
   the dispatch and records `hold` (reason `human_gate:<step>`). The workflow
   stops; the step's output stays posted on the issue for human review.
2. A human reviews the output and runs `/workflow resume [optional feedback]`.
   This re-runs the gated step with the human's text injected into the agent's
   context.
3. On that re-run the gate does **not** fire again вАФ the human's resume is the
   authorization. If the agent approves immediately it advances to the next
   step; if it must rework based on the feedback, the result still advances
   once it emits `advance`.
4. If the agent `loop`s after a resume, the flow is auto-triggered again, and
   the final `advance` re-triggers the gate: fresh human validation is required.

The gate intercepts only `advance` (and an absent dispatch). `terminate` is
never intercepted, and `loop` retries within a step are not intercepted.

---

## Script Steps

A step can be a **deterministic script run** instead of an agent: the engine
executes a repo-local script, posts its raw output as a comment, and advances
to the next step. The `script:` key is itself the step source (the nested
object name is the kind вАФ no separate `kind` field), like `agent:` or
`workflow:`.

```yaml
steps:
  - name: run-tests
    script:
      path: scripts/test.sh   # required, repo-relative
      args: ["--unit"]        # optional argv list (no shell interpolation)
      timeout_minutes: 15     # optional, >= 1 (default 30, capped at 60)
    next: review
```

Mechanics:

1. The resolver detects the `script:` source and emits the execution contract
   (`RUN_SCRIPT=true`, `SCRIPT_PATH`, `SCRIPT_ARGS`, `SCRIPT_TIMEOUT_MINUTES`)
   instead of a task assignment; no agent is run for that step.
2. The CI job executes the script in a dedicated "Run Script Step" step,
   bounded by a step-level timeout and an executor-level timeout.
3. The executor posts a structured comment вАФ a **PASS вЬЕ / FAIL вЪ†пЄП** banner,
   the script path, args, exit code, duration, and the raw output in a fenced
   code block (so trigger-like strings in the output stay inert) вАФ and
   advances the workflow state with a synthetic dispatch so the next step
   fires exactly as after an agent run.

Result semantics (feeds the existing `conditions` mechanism):

- Exit `0` вЖТ result `pass`; non-zero exit or timeout вЖТ result `fail`.
- If the step declares `conditions[]`, routing uses the matching branch
  (e.g. `on_result: fail вЖТ dev`); otherwise it follows the declared `next`.
- **A failing script still advances the workflow** in v1 вАФ it does not hold
  or terminate. The successor (usually an agent) receives the raw failure
  output and reacts. A `stop_on_fail` policy switch is a deferred
  enhancement.

Security notes:

- Scripts must live in the repo: `script.path` is repo-relative, absolute
  paths and `..` segments are rejected at parse time and re-verified at
  runtime (realpath containment inside the checkout).
- The script runs in the same CI job with the job's full environment and
  permissions вАФ the same trust level as the workflow YAML itself. Secret
  scrubbing / sandboxed execution are deferred enhancements.

---

## Built-in Workflows

| Workflow | File | One-line description |
|----------|------|---------------------|
| `default` | [`default.yaml`](../workflows/default.yaml) | Standard dev chain: analyze вЖТ context вЖТ plan вЖТ implement вЖТ review |
| `think` | [`think.yaml`](../workflows/think.yaml) | Thinking only вАФ analyze + context + plan, no implementation |
| `review-only` | [`review-only.yaml`](../workflows/review-only.yaml) | Standalone single-shot code review |
| `dev-review-loop` | [`dev-review-loop.yaml`](../workflows/dev-review-loop.yaml) | Reusable sub-workflow: one plan step with review, loops on rejection |
| `plan-orchestration` | [`plan-orchestration.yaml`](../workflows/plan-orchestration.yaml) | Full multi-step plan lifecycle sharing one PR |
| `spec-driven` | [`spec-driven.yaml`](../workflows/spec-driven.yaml) | Spec вЖТ stub (human-gated) вЖТ implement |
| `spec-driven-impl` | [`spec-driven-impl.yaml`](../workflows/spec-driven-impl.yaml) | Plan вЖТ stub (human-gated) вЖТ implement |
| `debug` | [`debug.yaml`](../workflows/debug.yaml) | Direct single-agent debug call |
| `demo-composition` | [`demo-composition.yaml`](../workflows/demo-composition.yaml) | Demo of sub-workflow composition |

### default

```
analyze (genius) вЖТ context (ctx) вЖТ plan (genius) вФАвФАapprovedвФАвФАвЦЇ implement (dev) вЖТ review (review) вЧЖ
                                                    вФВrejected
                                                    вФФвФАвФАвФАвФАвФАвФАвФАвФАвЦЇ context (loop)
```

Trigger: `@@genius`. The standard development cycle used when a human hands a
problem to the system. The `plan` step can loop back to `context` when the
plan is rejected (insufficient context).

Example trigger:

```
@@genius implement retry logic in the circuit breaker
```

### think

```
analyze (genius) вЖТ context (ctx) вЖТ plan (genius) вЧЖ
                                вФФвФА rejected вФАвЦЇ context (loop)
```

Trigger: `@@genius`. Pure analysis вАФ ends after the plan/answer is produced;
nothing is implemented. Use for "help me understand / define X" requests.

### review-only

```
review (review) вЧЖ
```

Trigger: `@@review`. Standalone review of code without a prior implementation
step in the same workflow вАФ used for ad-hoc review requests and nightly review
(issue #161).

### dev-review-loop

```
implement (dev) вЖТ review (review) вФАвФАapprovedвФАвФАвЦЇ вЧЖ
                              вФФвФАrejectedвФАвЦЇ implement (loop)
```

Trigger: `@@dev`. A **reusable sub-workflow**: it executes a *single* plan
step with review and loops until the reviewer approves. It is not typically
started directly вАФ it is referenced per-step from a dynamically generated
`tmp_impl/workflow.yaml` (see `plan-orchestration`).

### plan-orchestration

```
architect (genius) вЖТ context (ctx) вЖТ plan (genius) вФАвФАapprovedвФАвФАвЦЇ scaffold (dev, skill: scaffold-plan)
                                                   вФВrejected                вФВ
                                                   вФФвФАвФАвФАвФАвЦЇ plan (loop)       вЦЉ
                                                            sub_impl (workflow: tmp_impl/workflow.yaml)
                                                                         вФВ
                                                                         вЦЉ
                                                       sub_clean (dev, skill: plan-cleanup)
                                                                         вФВ
                                                    вФМвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвЦЉвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФР
                                                    вЦЉ                                         вФВ
                                             final_review (review) вФАвФАrejectedвФАвФАвЦЇ final_fix (dev, skill: final-fix)
                                                                    вФВapproved          вФВ
                                                                    вФФвЦЇвЧЖвЧДвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФАвФШ
```

Trigger: `@@genius`. The full lifecycle for a **multi-step plan that shares a
single PR** (issue #462):

1. `architect` / `context` / `plan` вАФ genius produces a structured plan; a
   human approves it (reject вЖТ re-plan loop).
2. `scaffold` вАФ dev converts the approved `<planWorkflow>` JSON into
   `tmp_impl/workflow.yaml`, a per-step sub-workflow (skill: `scaffold-plan`).
3. `sub_impl` вАФ **mounts** the generated sub-workflow as one step. Each plan
   step runs through `dev-review-loop` (implement вЖТ review, loop on
   rejection).
4. `sub_clean` вАФ dev removes the `tmp_impl/` scaffolding (skill:
   `plan-cleanup`).
5. `final_review` вЖФ `final_fix` вАФ one whole-PR review; rejections are
   addressed across all plan steps (skill: `final-fix`) and re-reviewed.

This is the main demonstration of **sub-workflow composition** вАФ a step
declaring `workflow:` instead of `agent:`, with a runtime-generated path.

### spec-driven

```
spec (po) вФАвФАrejectedвФАвФАвЦЇ spec
     вФВapproved
     вЦЉ
architect_prepare_ctx (genius) вЖТ ctx_architect (ctx) вЖТ architect_review_ctx (genius)
                                                        вФВrejected
                                                        вФФвЦЇ ctx_architect (loop)
     вФВapproved/default
     вЦЉ
stub (dev, skill: stub-only) вЧДвФАвФАrejectedвФАвФА stub_review (review)
     вФВ                                       вФВapproved/default
     вЦЉ                                       вЦЉ
                          stub_approval (genius, human_gate) вЧДвФАвФАrejected
     вФВapproved/default                            вФВ
     вЦЉ                                            вЦЉ
implement (dev) вФАвФАrejectedвФАвФА review (review) вЖТ вЧЖ
                вЦ≤______________________________вФВ
```

Trigger: `@@po`. Two **human gates**: the spec must be approved before any
architecture work, and the stubs must be approved before real implementation.
The `stub` step is constrained by the `stub-only` skill (stubs/tests only, no
logic). Review rejects send the dev back to fixing stubs or implementation.

### spec-driven-impl

```
analyze (genius) вЖТ context (ctx) вЖТ plan (genius) вФАвФАapprovedвФАвФАвЦЇ stub (dev, stub-only)
                                                   вФВrejected          вФВ
                                                   вФФвЦЇ context         вЦЉ
                                                            stub_review (review)
                                                                     вФВ
                                                     stub_approval (genius, human_gate)
                                                                     вФВapproved
                                                                     вЦЉ
                                                          implement (dev) вЧЖ
```

Trigger: `@@genius`. Same gate structure as `spec-driven`, but starts from the
standard analyze/context/plan chain instead of a PO spec. The stub review +
human approval is the quality gate before implementation.

### debug

```
debug (debug) вЧЖ
```

Trigger: `@@debug`. Single-agent call вАФ the debug agent investigates unusual
agent behavior from past run logs and artifacts.

### demo-composition

```
analyze (genius) вЖТ subtask (workflow: review-only) вЖТ finalize (genius) вЧЖ
```

Trigger: `@@genius`. Minimal example of **sub-workflow composition**: the
`subtask` step mounts the entire `review-only` workflow as one step of the
parent.

---

## Writing a Custom Workflow

Create `workflows/<name>.yaml`:

```yaml
name: my-workflow
description: "What this workflow does"
trigger:
  agents: [genius]
steps:
  - name: step_one
    agent: genius
    next: step_two
  - name: step_two
    agent: dev
    skill: [python]
    next: null
```

Rules:

- `name` must match the filename (without `.yaml`).
- Start by typing `@@<trigger agent>` in an issue, or use
  `/workflow my-workflow` to force it on an existing issue.
- Local files take precedence over remotely fetched workflows of the same name
  (zero-copy fetch вАФ see [features.md](./features.md#remote-workflows-zero-copy)).

**Reusable sub-workflows** are just workflows referenced by `workflow:` in a
step вАФ `dev-review-loop` and `review-only` are the built-in examples. The
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
