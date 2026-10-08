# Workflows

Declarative multi-step agent chains. A workflow is a YAML file in `workflows/`
that names an ordered set of steps; each step runs one agent (or mounts a
sub-workflow), and routing between steps is driven by `next` and `conditions`.
The full key-by-key grammar lives in
[reference.md — Workflow YAML Grammar](./reference.md#workflow-yaml-grammar).

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
- `complexity: low|medium|high` declares the complexity tier for that step's
  agent invocation (see [Per-step Complexity](#per-step-complexity)).
- `prompt_replace: true` makes the step `prompt:` *replace* the role prompt
  entirely, so the role's generic flow cannot redefine the step's scope
  (see [Step Instructions](#step-instructions-prompt)).
- `human_gate: true` marks a step as a **security boundary**: the workflow is
  held after that step until a human releases it (see [Human Gates](#human-gates)).
- Workflows can be started by an `@@mention` (matching `trigger.agents`), a
  `/nebuko <name>` command, or a `dispatch` field pointing at them. Human
  commands override state-based routing — see the routing cascade in
  [architecture.md](./architecture.md#routing-cascade).

---

## Step Instructions (`prompt`)

A step can carry a `prompt:` field with a standing instruction that is
injected into that step's agent context every time the step runs — useful for
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
that step — it governs the format and behavior of that step. The issue body
remains the *task-specific* content and wins on task specifics; the role prompt
stays the base identity. Injection order in the agent context:
**role identity → step instruction → issue body/history** (section header:
`### 🔴 STEP INSTRUCTION (from workflow.yaml)`).

**Mechanics.** The resolver writes the prompt verbatim to
`/tmp/step_prompt.md` and emits `STEP_PROMPT_FILE` (path only — the prompt may
contain newlines). When the step has no `prompt`, the variable is empty and any
stale file is removed, so existing workflows are unaffected. Multi-line YAML
block scalars (`|` / `>`) are supported.

**Replacement mode (`prompt_replace: true`).** By default the step prompt is
*additive*: the role prompt stays the base identity. Setting
`prompt_replace: true` alongside `prompt:` makes the step prompt the **entire**
agent context — the role prompt (e.g. `dev.md`) and its generic
Understand / Plan / Implement / Verify flow are suppressed. Use it to
structurally scope a step to a single job that the role's default flow would
otherwise broaden (e.g. a scaffold step that must materialize a plan and not
implement it). It is ignored when `prompt:` is unset, so existing workflows are
unaffected.

**`prompt:` vs `skill:`.** Use `prompt:` for short, self-contained,
per-step directives that should live inline in the workflow. Use
`skill: [...]` for richer, multi-file, or reusable knowledge files in
`skills/`. They compose: a step may have both.

---

## Per-step Complexity

A step can declare the complexity tier for its own agent invocation with
`complexity: low|medium|high`. This lets a workflow author cheaply price each
step instead of paying the default `medium` on every one — for example a
triage/`analyze` step that only drafts a context query can run `low`, while an
architecture `plan` step can run `high`:

```yaml
steps:
  - name: analyze
    agent: genius
    complexity: low      # triage + ctx-query drafting is cheap
    next: context

  - name: plan
    agent: genius
    complexity: high     # architecture work deserves the top tier
    next: stub
```

The step **selects** the tier; the agent config **defines** it. What a tier
means (model swap, temperature floor, reasoning effort) lives in
`agents.<name>.complexity_overrides.<tier>` — see
[configuration.md](./configuration.md#complexity-overrides-per-tier-configuration).

**Precedence** (highest first):

1. an explicit `complexity` in the agent's `dispatch_next` (the agent in the
   loop knows best), then
2. the step's `steps[].complexity`, then
3. `medium`.

The resolved tier is exported to the run as `CONFIG_COMPLEXITY` and drives the
tier machinery at LLM-config resolution time. Note that the *first* step of a
workflow has no preceding dispatch, so its declared `complexity` is the only
thing that can lower its cost — this is the motivating case for the field
(issue #787).

---

## Human Gates

`human_gate: true` is a security boundary enforced by the engine — not a
routing choice made by the agent. It guarantees that **no LLM can advance the
workflow past a gated step on its own**; only a human action releases it.

Mechanics:

1. The gated step's agent finishes and emits `advance`. The engine intercepts
   the dispatch and records `hold` (reason `human_gate:<step>`). The workflow
   stops; the step's output stays posted on the issue for human review.
2. A human reviews the output and runs `/nebuko resume [optional feedback]`.
   This re-runs the gated step with the human's text injected into the agent's
   context.
3. On that re-run the gate does **not** fire again — the human's resume is the
   authorization. If the agent approves immediately it advances to the next
   step; if it must rework based on the feedback, the result still advances
   once it emits `advance`.
4. If the agent `loop`s after a resume, the flow is auto-triggered again, and
   the final `advance` re-triggers the gate: fresh human validation is required.

**Engine-owned human notification (issue #795).** Agents never ping a human.
When a run stops for human attention the engine itself posts one
`HumanActionNotice` comment naming the workflow starter
(`@started_by`), so the person who launched the workflow is asked for
validation. The notice is classified from the run outcome:

| Stop | Notice |
|---|---|
| `hold`, reason `human_gate:<step>` | *awaiting approval* |
| `hold`, any other reason (incl. no-action default) | *awaiting input* |
| terminal step, on the run that produced its output | *workflow complete* |
| `soft_kill` while the soft-retry budget remains | none (transient retry) |
| terminal `SKIP_AGENT` on a follow-up run | none (nothing new to act on) |

The recipient is the **workflow starter** (`WorkflowState.started_by`, captured
from the first trigger and carried forward), falling back to the issue author
and then the trigger author. Agent-authored comment bodies are made
notification-inert by the publish path, so an agent mentioning `@someone` is
readable but does not notify; fenced blocks and inline code are left verbatim
(GitHub never notifies for a mention there, so rewriting one would only corrupt
the snippet).

The gate intercepts only `advance` (and an absent dispatch). `terminate` is
never intercepted, and `loop` retries within a step are not intercepted.

---

## Script Steps

A step can be a **deterministic script run** instead of an agent: the engine
executes a repo-local script, posts its raw output as a comment, and advances
to the next step. The `script:` key is itself the step source (the nested
object name is the kind — no separate `kind` field), like `agent:` or
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
3. The executor posts a structured comment — a **PASS ✅ / FAIL ⚠️** banner,
   the script path, args, exit code, duration, and the raw output in a fenced
   code block (so trigger-like strings in the output stay inert) — and
   advances the workflow state with a synthetic dispatch so the next step
   fires exactly as after an agent run.

Result semantics (feeds the existing `conditions` mechanism):

- Exit `0` → result `pass`; non-zero exit or timeout → result `fail`.
- If the step declares `conditions[]`, routing uses the matching branch
  (e.g. `on_result: fail → dev`); otherwise it follows the declared `next`.
- **A failing script still advances the workflow** in v1 — it does not hold
  or terminate. The successor (usually an agent) receives the raw failure
  output and reacts. A `stop_on_fail` policy switch is a deferred
  enhancement.

Security notes:

- Scripts must live in the repo: `script.path` is repo-relative, absolute
  paths and `..` segments are rejected at parse time and re-verified at
  runtime (realpath containment inside the checkout).
- The script runs in the same CI job with the job's full environment and
  permissions — the same trust level as the workflow YAML itself. Secret
  scrubbing / sandboxed execution are deferred enhancements.

---

## Built-in Workflows

| Workflow | File | One-line description |
|----------|------|---------------------|
| `default` | [`default.yaml`](../defaults/workflows/default.yaml) | Standard dev chain: analyze → context → plan → implement → review |
| `think` | [`think.yaml`](../defaults/workflows/think.yaml) | Thinking only — analyze + context + plan, no implementation |
| `review-only` | [`review-only.yaml`](../defaults/workflows/review-only.yaml) | Standalone single-shot code review |
| `dev-review-loop` | [`dev-review-loop.yaml`](../defaults/workflows/dev-review-loop.yaml) | Reusable sub-workflow: one plan step with review, loops on rejection |
| `plan-orchestration` | [`plan-orchestration.yaml`](../defaults/workflows/plan-orchestration.yaml) | Full multi-step plan lifecycle sharing one PR |
| `spec-driven` | [`spec-driven.yaml`](../defaults/workflows/spec-driven.yaml) | Spec → stub (human-gated) → implement |
| `spec-driven-impl` | [`spec-driven-impl.yaml`](../defaults/workflows/spec-driven-impl.yaml) | Plan → stub (human-gated) → implement |
| `debug` | [`debug.yaml`](../defaults/workflows/debug.yaml) | Direct single-agent debug call |
| `demo-composition` | [`demo-composition.yaml`](../defaults/workflows/demo-composition.yaml) | Demo of sub-workflow composition |

### default

```
analyze (genius) → context (ctx) → plan (genius) ──approved──► implement (dev) → review (review) ◆
                                                    │rejected
                                                    └────────► context (loop)
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
analyze (genius) → context (ctx) → plan (genius) ◆
                                └─ rejected ─► context (loop)
```

Trigger: `@@genius`. Pure analysis — ends after the plan/answer is produced;
nothing is implemented. Use for "help me understand / define X" requests.

### review-only

```
review (review) ◆
```

Trigger: `@@review`. Standalone review of code without a prior implementation
step in the same workflow — used for ad-hoc review requests and nightly review
(issue #161).

### dev-review-loop

```
implement (dev) → review (review) ──approved──► ◆
                              └─rejected─► implement (loop)
```

Trigger: `@@dev`. A **reusable sub-workflow**: it executes a *single* plan
step with review and loops until the reviewer approves. It is not typically
started directly — it is referenced per-step from a dynamically generated
`tmp_impl/workflow.yaml` (see `plan-orchestration`).

### plan-orchestration

```
architect (genius) → context (ctx) → plan (genius) ──approved──► scaffold (dev, prompt_replace)
                                                   │rejected                │
                                                   └────► plan (loop)       ▼
                                                            sub_impl (workflow: tmp_impl/workflow.yaml)
                                                                         │
                                                                         ▼
                                                       sub_clean (dev, skill: plan-cleanup)
                                                                         │
                                                    ┌────────────────────▼────────────────────┐
                                                    ▼                                         │
                                             final_review (review) ──rejected──► final_fix (dev, skill: final-fix)
                                                                    │approved          │
                                                                    └►◆◄────────────────┘
```

Trigger: `@@genius`. The full lifecycle for a **multi-step plan that shares a
single PR** (issue #462):

1. `architect` / `context` / `plan` — genius produces a structured plan; a
   human approves it (reject → re-plan loop).
2. `scaffold` — dev converts the approved `<planWorkflow>` JSON into
   `tmp_impl/workflow.yaml`, a per-step sub-workflow.
   The step sets `prompt_replace: true`, so the `dev.md` role prompt and its
   generic Implement flow are suppressed and the step scope is exactly the
   scaffold instruction.
3. `scaffold_review` (review) verifies `tmp_impl/workflow.yaml`
   faithfully materializes the approved `<planWorkflow>` block (same steps,
   same order; nothing implemented outside `tmp_impl/`); a mismatch loops back
   to `scaffold`.
4. `sub_impl` — **mounts** the generated sub-workflow as one step. Each plan
   step runs through `dev-review-loop` (implement → review, loop on
   rejection).
5. `sub_clean` — dev removes the `tmp_impl/` scaffolding (skill:
   `plan-cleanup`).
6. `final_review` ↔ `final_fix` — one whole-PR review; rejections are
   addressed across all plan steps (skill: `final-fix`) and re-reviewed.

This is the main demonstration of **sub-workflow composition** — a step
declaring `workflow:` instead of `agent:`, with a runtime-generated path.

**Entering mid-lifecycle (issue #789).** When a plan already exists on the
issue (for example from an earlier deep-thinking run), you can enter this
workflow without replaying `architect`:

```
/nebuko run workflow --name plan-orchestration --step plan
```

Use `--step plan` to have genius convert/re-validate the existing plan and
re-arm the human gate; `--step scaffold` converts it directly (bypassing the
gate — `run` is human-only, so that is an explicit human choice). `--step
sub_impl` requires `tmp_impl/workflow.yaml` to be scaffolded already, else the
entry fails loudly rather than starting from the first step.

### spec-driven

```
spec (po) ──rejected──► spec
     │approved
     ▼
architect_prepare_ctx (genius) → ctx_architect (ctx) → architect_review_ctx (genius)
                                                        │rejected
                                                        └► ctx_architect (loop)
     │approved/default
     ▼
stub (dev, skill: stub-only) ◄──rejected── stub_review (review)
     │                                       │approved/default
     ▼                                       ▼
                          stub_approval (genius, human_gate) ◄──rejected
     │approved/default                            │
     ▼                                            ▼
implement (dev) ──rejected── review (review) → ◆
                ▲______________________________│
```

Trigger: `@@po`. Two **human gates**: the spec must be approved before any
architecture work, and the stubs must be approved before real implementation.
The `stub` step is constrained by the `stub-only` skill (stubs/tests only, no
logic). Review rejects send the dev back to fixing stubs or implementation.

### spec-driven-impl

```
analyze (genius) → context (ctx) → plan (genius) ──approved──► stub (dev, stub-only)
                                                   │rejected          │
                                                   └► context         ▼
                                                            stub_review (review)
                                                                     │
                                                     stub_approval (genius, human_gate)
                                                                     │approved
                                                                     ▼
                                                          implement (dev) ◆
```

Trigger: `@@genius`. Same gate structure as `spec-driven`, but starts from the
standard analyze/context/plan chain instead of a PO spec. The stub review +
human approval is the quality gate before implementation.

### debug

```
debug (debug) ◆
```

Trigger: `@@debug`. Single-agent call — the debug agent investigates unusual
agent behavior from past run logs and artifacts.

### demo-composition

```
analyze (genius) → subtask (workflow: review-only) → finalize (genius) ◆
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
  `/nebuko my-workflow` to force it on an existing issue.
- Local files take precedence over remotely fetched workflows of the same name
  (zero-copy fetch — see [features.md](./features.md#remote-workflows-zero-copy)).

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
