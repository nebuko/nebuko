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
- `skill: [...]` attaches extra prompt context from `defaults/skills/` to that step.
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
    agent: architect
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
`defaults/skills/`. They compose: a step may have both.

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
    agent: architect
    complexity: low      # triage + context-query drafting is cheap
    next: context

  - name: plan
    agent: architect
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
workflow past a gated step on its own**; only an identity-verified human
command releases it.

A gate release is an **engine-owned state mutation** (`GateDecision`), never an
agent action. The releasing command is recognized at comment-ingestion time and
keyed on the **GitHub event author** — never on comment-body markers, which are
agent-writable.

Mechanics:

1. The gated step's agent finishes and emits `advance` (or any other
   completing dispatch). The engine intercepts it and records `hold` (reason
   `human_gate:<step>`). The workflow stops; the step's output stays posted on
   the issue for human review. **Every** advancing dispatch is intercepted —
   the gate has no bypass.
2. An authorized human (GitHub `OWNER` / `MEMBER` / `COLLABORATOR`) reviews the
   output and runs one of:

   ```
   /nebuko approve gate [--feedback "..."]
   /nebuko reject gate  [--feedback "..."]
   ```

   The command is handled **inline by the engine** and is never routed to an
   agent.
3. The engine records a `GateDecision` (`status`, `decided_by`, `decided_at`,
   `feedback`, and the exact hold instance it consumes) — the only artifact
   that can move the workflow past the gate — and routes from it:
   - **approved** → the gated step's `on_result: approved` successor;
   - **rejected with feedback** → the gated step is **re-run** with the
     feedback injected into its context; the gate re-arms when that run
     completes, so fresh human validation is required;
   - **rejected without feedback** → the `on_result: rejected` successor.
4. Decisions are **single-shot**: a `GateDecision` binds to exactly one
   `(workflow, step, position)` hold instance and is consumed on routing, so
   replaying an old command is a no-op.

**Agents complete a gated step with `advance`, never `hold`.** The gate is
released only by the human command above, so the agent's job is to deliver its
output and advance; the engine converts that completing dispatch into the hold.
An explicit `hold` would stop *before* the gate and require a second, non-gate
resume — the double-approval anti-pattern.

**Non-authoritative agent classification.** Agents may still read and classify
human intent for UX (a role prompt may note "the human approved"), but that
classification is **not** what releases a gate — only the engine-recorded
`GateDecision` does.

**Identity is authoritative.** A gate command is accepted only when its GitHub
event author is a non-bot user whose `author_association` is
`OWNER` / `MEMBER` / `COLLABORATOR`, and the comment body carries no machine
token (`<!-- bot-... -->`, `AGENT_RUN_CONFIG`, …). A comment authored by the CI
bot that merely *contains* `/nebuko approve gate` is inert, and so is a
privileged human echoing agent output that carries a machine token.

**`/nebuko resume` is unchanged for non-gated holds.** It still releases a
`soft_kill` exhaustion halt (issue #704), but on a `human_gate:*` hold it is a
no-op that points at the gate commands — one release path per hold class.

**Engine-owned human notification (issue #795).** Agents never ping a human.
When a run stops for human attention the engine itself posts one
`HumanActionNotice` comment naming the workflow starter
(`@started_by`), so the person who launched the workflow is asked for
validation. The notice is classified from the run outcome:

| Stop | Notice |
|---|---|
| `hold`, reason `human_gate:<step>` | *awaiting approval* |
| `hold`, any other reason (incl. a rudderless no-action default, §5 of [handoff-and-callback.md](./handoff-and-callback.md)) | *awaiting input* |
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
   (`RUN_SCRIPT=true`, `SKIP_AGENT=true`, `SCRIPT_PATH`, `SCRIPT_ARGS`,
   `SCRIPT_TIMEOUT_MINUTES`, `WORKFLOW_STEP`) instead of a task assignment; no
   agent is run for that step.
2. The engine runs the script from `[phase:action]` (bounded by the optional
   `timeout_minutes`), capturing stdout and stderr **merged**, and posts **two**
   comments — the same comment lifecycle as an agent step:
   - a *start notice* on entry: `<step> command started` — View workflow run
     logs. It carries no state block and **no** `<!-- workflow-continue -->`
     token (a token here would re-trigger the gate before the command finished);
   - a *completion* comment carrying the workflow state
     (`<!-- bot-workflow-terminal -->` + `<!-- bot-command-<step> -->` +
     `<!-- workflow-continue -->` + the embedded state block). This comment is
     the step's registered output.
3. The completion body is a **`<step> command succeeded` / `command failed`**
   banner — the step name, exit code and duration only, **never the command
   line** (path/args may carry secrets) — followed by a collapsed
   `<details><summary>command output</summary>` fenced block with the captured
   output (so trigger-like strings in the output stay inert). Oversized output
   is truncated head+tail with a marker to stay under GitHub's comment-size
   limit.
4. The completion comment is registered as the step output
   (`current_context.step_outputs` → `{step, comment_id}`), so a downstream
   `inputs: [<step>]` binds to it exactly like an agent answer (ADR-0013), and
   the `<!-- workflow-continue -->` token re-triggers the workflow gate so the
   chain continues automatically. The script's distinct `bot-command-*` tag is
   never counted as an agent turn by the circuit breaker.

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
- The command line (`path` + `args`) is deliberately **not echoed** in the
  posted comments; only the step name, exit code and duration appear in the
  banner. The captured *output* is posted as-is — the same trust level as an
  agent answer.
- The script runs in the same CI job with the job's full environment and
  permissions — the same trust level as the workflow YAML itself. Secret
  scrubbing / sandboxed execution are deferred enhancements.

---

## Step Hooks

An **agent** step can declare two deterministic hooks that run **in the session
workspace** around the agent invocation (issue #884):

- `pre_hook` runs immediately **before** the agent is invoked (e.g. initialize a
  dev environment);
- `post_hook` runs immediately **after** the agent attempt completes (e.g.
  gather files/stats).

Both use the same shape as a `script` step — a repo-relative `path`, an optional
argv `args` list, and an optional `timeout_minutes`. The hook's YAML key is the
discriminator; there is no separate `kind` field.

```yaml
steps:
  - name: implement
    agent: dev
    pre_hook:
      path: scripts/init-dev-env.sh   # required, repo-relative
      args: ["--full"]                # optional argv list (no shell interpolation)
      timeout_minutes: 10             # optional, >= 1 (default 30, capped at 60)
    post_hook:
      path: scripts/collect-stats.sh
```

Lifecycle (inside `[phase:agent]`, after the pre-agent state is staged):

1. `pre_hook` runs in the session workspace with the run's environment. It is
   **fail-closed**: a non-zero exit, timeout, or failed exec **skips the agent**
   and fails the step through the same path as an agent crash (the run posts its
   usual failure notice and holds).
2. The agent is invoked (`notify_agent_started` + the model run).
3. `post_hook` runs in a finally-style position — after the agent attempt
   returns, raises, or is skipped for a failed `pre_hook`. It is **fail-open**:
   its own failure/timeout is logged (`post_hook failed: …`) but **never** changes
   the step's outcome, dispatch, or routing.

Failure semantics summary:

| Hook | When it runs | On its own failure |
| --- | --- | --- |
| `pre_hook` | before the agent | agent skipped, step fails (fail-closed) |
| `post_hook` | after the agent attempt (finally-style) | recorded, outcome unchanged (fail-open) |

Scope and security:

- Hooks are valid on **`agent` steps only**; declaring them on a `script`,
  `workflow`, or `repo` step is rejected at parse time.
- `path` must be repo-relative: absolute paths and `..` segments are rejected at
  parse time (the same rule as `script.path`).
- Hooks run in the same CI job and session workspace with the job's full
  environment and permissions — the same trust level as the workflow YAML and the
  `script` step. Sandboxed execution is a deferred enhancement.

v1 boundaries (deferred): string shorthand (`pre_hook: scripts/x.sh`), ordered
lists of hooks, hooks on `script`/`workflow`/`repo` steps, an `on_fail` /
`stop_on_fail` policy field, and posting hook output as a separate comment.

---

## Typed Inputs and Step Outputs

A workflow is a **typed function** (issue #813): it declares `inputs` (its
parameters) and `outputs` (its return values), and the caller supplies concrete
values at each `workflow` call site. This is how the engine guarantees a step
receives the artifacts it needs — the plan, a `git diff`, test results —
**deterministically**, without the model spending turns fetching them or
hallucinating their absence (see [ADR-0009](./adr/0009-workflows-as-typed-functions.md)).

```yaml
# leaf.yaml — declares what it needs and what it returns
name: leaf
inputs:
  - name: run_test
    kind: exec        # exec = a deterministic provider command the engine runs
  - name: greeting
    kind: str         # str  = a constant passed through
outputs:
  - implement
steps:
  - name: implement
    agent: dev
    inputs:
      run_test: input.run_test   # materialized just before this agent runs
      greeting: input.greeting
    next: null
```

```yaml
# caller.yaml — binds the arguments at the call site
name: caller
steps:
  - name: build
    workflow: leaf
    inputs:
      run_test:
        exec: ["pytest", "."]    # a literal command binding (block style)
      greeting:
        str: "hello"
```

Mechanics:

1. **Call-site binding** — a `workflow` call step passes one value per declared
   input. A binding has exactly one source key: `exec` (argv), `str`
   (constant), or a bare string meaning a **`ref`** (`diff: input.diff`).
2. **Kind-preserving lazy forwarding** — a `ref` to an `exec` input forwards the
   **command spec, not its output**. A super-parent can push a command through a
   middle workflow to a leaf; materialization happens **exactly once, at the
   deepest consuming step**, so freshness is depth-invariant.
3. **Mandatory injection** — right before the consuming agent step, the engine
   runs each `exec` and injects the captured stdout as a labeled context block.
   `required: true` (default) fails the step **before the agent is invoked** if
   the command fails; `required: false` injects `_(unavailable: …)_`.
4. **Bounded nesting** — the call-frame chain is capped (`MAX_CALL_FRAMES`);
   an over-deep chain fails the step deterministically before the agent runs.
   The frame chain is derived from the run's carried-forward mount position and
   decoded at the engine's input-materialization seam, where the cap is
   enforced.

Wiring is checked offline by [`python -m engine lint`](./lint.md) (rules
[L8–L11](./lint.md#rule-reference)): every declared input bound exactly once per
call site, kinds matched, references resolvable in scope, every declared
input consumed or forwarded, and every prompt reference naming a declared step.

### Step Outputs — A Step's Output Is Its Comment

Data flow **between steps of the same workflow** needs no declaration: a step's
output *is* the comment it posts, addressed by the **step name** (issue #756,
[ADR-0013](./adr/0013-step-outputs-are-step-comments.md)). A downstream step
consumes an upstream step's comment through either surface, both resolved by the
engine **before the agent runs**:

- the deterministic `inputs` seam — `inputs: [plan]` (list shorthand) or the
  alias mapping `inputs: {reviewed_plan: plan}`; and
- `{{ <step> }}` inside `steps[].prompt`.

```yaml
steps:
  - name: plan
    agent: architect
    next: implement

  - name: implement
    agent: dev
    inputs: [plan]                 # the plan comment, materialized

  - name: review
    agent: review
    inputs: [plan, implement]      # fan-in: plan + the dev output
    prompt: |
      Review the implementation against the plan.
      Plan under review: {{ plan }}
    conditions:
      - on_result: approved
        next: null
      - on_result: implement
        next: plan
```

- The value is the referenced step's **full comment, state-stripped** — exactly
  what agents see in the thread. There is **no truncation**; the only bounds are
  the pre-existing comment-size limits.
- Only a **locator** (`{step, comment_id}`) is persisted, under
  `current_context["step_outputs"]` in the `WORKFLOW_STATE` v1 comment — never
  the body. The body is read back from the bounded thread snapshot.
- Resolution is **single-pass**: a substituted body is never re-scanned, so a
  step that quotes `{{ … }}` cannot induce recursion.
- An unresolved reference degrades to `_(unavailable: <step>)_`; the linter
  (`L10-input-ref`, `L11-prompt-ref`) reports it ahead of the run.
- `inputs: [plan]` is a **required** binding: `input.<name>` workflow
  parameters from ADR-0009 are unchanged and take precedence in the scope.

**Status.** Both paths are shipped: the input path (declared `inputs`,
call-site binding, kind-preserving forwarding, just-in-time `exec`
materialization, mandatory injection, `MAX_CALL_FRAMES`) and the step-output
path (`inputs: [<step>]`, `{{ <step> }}` prompt references, locator storage,
locator backfill at publish and read time). This supersedes the earlier
"output path is forward-looking" note: the named-output grammar
(`outputs:` / `<step>.<name>`) was **removed** in favour of step-name
references; a leftover `steps[].outputs:` key or dotted ref fails at load time
with a #756 migration hint.

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
| `demo-composition` | [`demo-composition.yaml`](../defaults/workflows/demo-composition.yaml) | Demo of sub-workflow composition |

### default

```
analyze (architect) → context (context) → plan (architect) ──approved──► implement (dev) → review (review) ◆
                                                    │rejected
                                                    └────────► context (loop)
```

Trigger: `@@architect`. The standard development cycle used when a human hands a
problem to the system. The `plan` step can loop back to `context` when the
plan is rejected (insufficient context).

The `plan` step is a single `human_gate:plan`: @@architect emits **one** planning
artifact — visible `Context (root cause)`, `Decision` (design + Object Design
Anchor), an `Approaches considered` table, and the `Object Design Contract`, with
the `Implementation Plan` collapsed in a `<details>` block — and ends with
`advance`. The engine intercepts that advance into the gate hold, so
the human runs exactly **one** `/nebuko approve gate` before @@dev is routed.

Example trigger:

```
@@architect implement retry logic in the circuit breaker
```

### think

```
analyze (architect) → context (context) → plan (architect) ◆
                                └─ rejected ─► context (loop)
```

Trigger: `@@architect`. Pure analysis — ends after the plan/answer is produced;
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
architect (architect) → context (context) → plan (architect) ──approved──► scaffold (dev, prompt_replace)
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

Trigger: `@@architect`. The full lifecycle for a **multi-step plan that shares a
single PR** (issue #462):

1. `architect` / `context` / `plan` — architect produces a structured plan; a
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

Use `--step plan` to have architect convert/re-validate the existing plan and
re-arm the human gate; `--step scaffold` converts it directly (bypassing the
gate — `run` is human-only, so that is an explicit human choice). `--step
sub_impl` requires `tmp_impl/workflow.yaml` to be scaffolded already, else the
entry fails loudly rather than starting from the first step.

**Restarting at a nested plan step (issue #822).** A dotted selector descends
the mount chain, so you can re-enter the scaffolded plan at a specific item
without replaying the ones already done. For issue #648 — where plan `step_1`
and `step_2` are complete — restart at `step_3`:

```
/nebuko run workflow --name plan-orchestration --step sub_impl.step_3
```

This seeds a fresh run at `plan-orchestration/sub_impl → tmp-impl/step_3 →
dev-review-loop/implement`, so it runs `step_3…N` then `sub_clean` →
`final_review`; `step_1` and `step_2` are never executed. Add one more segment
to re-enter at a leaf inside the per-step loop — e.g. re-run only `step_3`'s
review once its implementation is done:

```
/nebuko run workflow --name plan-orchestration --step sub_impl.step_3.review
```

Each segment must match the scaffolded step name verbatim (`step_3`, not
`step3`). An unknown segment, a malformed path, or a mount whose
`tmp_impl/workflow.yaml` has not been scaffolded fails loudly rather than
silently starting from the first step.

### spec-driven

```
spec (po) ──rejected──► spec
     │approved
     ▼
architect_prepare_context (architect) → context_architect (context) → architect_review_context (architect)
                                                        │rejected
                                                        └► context_architect (loop)
     │approved/default
     ▼
stub (dev, skill: stub-only) ◄──rejected── stub_review (review)
     │                                       │approved/default
     ▼                                       ▼
                          stub_approval (architect, human_gate) ◄──rejected
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
analyze (architect) → context (context) → plan (architect) ──approved──► stub (dev, stub-only)
                                                   │rejected          │
                                                   └► context         ▼
                                                            stub_review (review)
                                                                     │
                                                     stub_approval (architect, human_gate)
                                                                     │approved
                                                                     ▼
                                                          implement (dev) ◆
```

Trigger: `@@architect`. Same gate structure as `spec-driven`, but starts from the
standard analyze/context/plan chain instead of a PO spec. The stub review +
human approval is the quality gate before implementation.

### demo-composition

```
analyze (architect) → subtask (workflow: review-only) → finalize (architect) ◆
```

Trigger: `@@architect`. Minimal example of **sub-workflow composition**: the
`subtask` step mounts the entire `review-only` workflow as one step of the
parent.

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
