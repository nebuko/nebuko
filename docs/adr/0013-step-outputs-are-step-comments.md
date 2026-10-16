# ADR-0013: Declared Step Outputs — A Step's Output Is Its Comment

**Status:** Accepted — 2026-10-11
**Decider:** olivier-norture
**Related:** #756 (this decision), #747 (Dify comparison), #813 / ADR-0009 (typed workflow inputs), #754 (deterministic `tool` steps), #755 (fan-in), ADR-0003 (context management)

## Context

The primary state channel between steps today is the **issue comment thread**:
each step posts a comment and the next agent reads the history (or a pinned
subset). There is no first-class "this step declares output X, later steps
reference X" contract. Dify's App DSL has exactly that — `{{#node_id.field#}}`
selectors — and builds every prompt from declared node outputs.

Per the #747 scope decision, **richer state storage belongs to the future SaaS
version**; the deliverable here is the **protocol first**, compatible with
comment-state. The design must therefore answer four questions without
introducing a new store: what an output *is*, how a downstream step *references*
it, *where the value lives*, and *how large it may grow*.

A first draft (v5 of the plan) proposed a per-step `outputs:` declaration
carrying a list of **named** outputs (`{{ plan.plan_doc }}`). Review rejected it:
every name would have resolved to the **same full comment**, so the naming layer
bought zero semantic value while costing grammar, config surface, lint rules,
and documentation. The simplification is the decision below.

## Decision

**A step's output is the comment it posts.** There is no declaration and no
output object: naming a step in data-flow position is the entire protocol.

### Grammar

- No `steps[].outputs` key. The pre-#756 declaration and its
  `<step>.<out>` reference form are **removed**; a leftover key or dotted ref
  fails at load time with a migration hint (see *Migration*).
  *Amended by #973:* a step may now declare the **shape of its own comment**
  with `steps[].output` (see [workflows.md](../workflows.md#declared-step-output--the-output-shape-issue-973)),
  and the dotted form is re-introduced **only** for a field that declaration
  names — `<step>.<field>` addresses one declared section. A dotted ref to an
  undeclared field is still a lint error.
- A data-flow reference is a **bare step name**. Two surfaces consume it:
  - the step `inputs:` seam — `inputs: [plan]` (list shorthand) or the mapping
    form `inputs: {context: plan}` for an alias; and
  - the step `prompt:` text — see *Prompt reference syntax*.
- The workflow-level `inputs`/`outputs` signature from ADR-0009 is **unchanged**;
  only the per-step named-output half of that ADR is superseded here.
- `input` is a **reserved namespace prefix**: `input.<name>` always addresses a
  workflow parameter, so a step named `input` is flagged by the linter.

### Prompt reference syntax

Inside `steps[].prompt`, a `{{ <step> }}` placeholder is replaced, by the engine
and **before the agent runs**, with the referenced step's full
state-stripped comment:

```
{{ <step> }}          # e.g. {{ plan }}
```

- Grammar: `{{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*}}` (`PROMPT_REF_PATTERN`).
- Resolution is **single-pass and non-recursive**: a substituted body is never
  re-scanned, so a step that itself quotes `{{ … }}` cannot induce recursion or
  injection amplification.
- An unresolved reference degrades to `_(unavailable: <step>)_`, matching the
  existing `input_wiring` degradation wording — the engine never fails a step
  over a missing prompt reference.
- `{{ … }}` is thus **reserved** inside `steps[].prompt`; there is no escape
  syntax in v1 (lint rule L11 is the guard).

### Storage — locators in `WORKFLOW_STATE` v1

The value lives in the **existing** `WORKFLOW_STATE` v1 JSON, under
`current_context["step_outputs"]`, as a list of **locators**:

```json
{"current_context": {"step_outputs": [{"step": "implement", "comment_id": "1234"}]}}
```

- `StepOutputRecord` is `{step, comment_id}`; uniqueness is on `step`; the
  latest write wins, so a loop re-run (e.g. `plan` after `rejected`)
  **re-points** the locator at the newest comment.
- `comment_id` is unknown when the state is built (the comment is posted
  afterwards), so it is **backfilled**: at publish time from `adapter.post()`'s
  return, and on the read path by index alignment (each state comment's
  embedded `step` → that comment's id).
- **Comment bodies are deliberately not stored.** The state block is
  re-embedded *untruncated* into every posted comment, and there is no
  `current_context` size guard, so persisting bodies would compound against
  GitHub's 65,536-char comment limit on every turn. Locators plus the existing
  bounded thread read (`thread_snapshot`) resolve bodies on demand.
- **No new store, no DB.** The SaaS runner's richer store remains out of scope.

### Size and truncation policy

**None.** Dify truncates variables at 2,000 characters and caps files at 50 MB;
both are **explicitly rejected** here. A step's output is the whole
state-stripped comment, and the only bounds are the pre-existing ones:

- `MAX_COMMENT_LENGTH = 60000` on an answer body, and
- GitHub's 65,536-character hard limit on a comment.

Storing locators rather than bodies is what makes this safe: resolution returns
text that already fits in a comment, because it *is* a comment. The known
pre-existing gap — no `current_context` size guard — is a **follow-up**, not a
goal of this ADR.

### Relationships

- **`dispatch.pin`** stays a *display* affordance for the thread snapshot. It is
  orthogonal to output resolution: pinned-comment injection into the prompt is
  not wired at runtime today (pre-existing; out of scope here).
- **`build_state.artifacts`** remain **file-path** hand-offs (the 50 MB-file
  question does not arise); step outputs are comment hand-offs. The two are
  complementary, not competing.
- **Tool steps (#754)**: a tool step's comment *is* its output — no special
  case. Fan-in **#755** is `inputs: [plan, implement]`: one step consuming N
  upstream step names, each resolved to its comment.
- **ADR-0009** otherwise stands; this ADR amends only its named-output clause.

### Migration

- `steps[].outputs:` → remove the key; reference the step name instead. The
  loader fails with: *"'outputs' was removed from a step by issue #756; a
  step's output is its posted comment, referenced by the step name…"*.
- `<step>.<out>` → `{{ <step> }}` (prompt) or `inputs: [<step>]`. A dotted
  non-`input.*` ref fails with a `#756` migration hint. *Amended by #973:* a
  dotted ref naming a field the step declares in its `output:` block is valid
  again and resolves to that section.
- `input.<name>` workflow-input references from ADR-0009 are **unchanged**.
- A step with no `inputs`/`prompt` behaves exactly as today: no prompt
  rewriting is required for existing workflows.

## Object design

- **`StepOutputRecord`** (`workflow_state/step_output_record.py`) — pydantic
  domain entity, the locator: `step: str`, `comment_id: str | None`. Methods
  `upsert_into`, `load_all`, `index`.
- **`ResolvedOutput`** (`workflow_state/features/resolve_step_output/`) —
  `@dataclass(frozen=True)` immutable feature result: `step`, `ok`, `body`,
  `detail`.
- **`resolve_step_output(records, comment_index, step)`** — pure join of the
  locator with the comment index; degradation reasons: `not recorded`,
  `no comment recorded`, `comment not found`.
- **`render_prompt_refs(prompt, resolved)`** — pure, single-pass substitution.
- **`build_comment_index(comments)`** (`thread_snapshot.py`) — maps
  `comment_id → state-stripped body`, delegating to
  `to_git_comment.strip_state_section` so a value is byte-identical to what
  `_agent_facing_body` shows agents.
- **One resolver, two surfaces**: `input_wiring.materialize(payload,
  step_scope=…)` resolves bare `<step>` refs to comments (list shorthand
  supported on `WorkflowStep`); the run loop applies `render_prompt_refs` to
  the step prompt. Both share `StepOutputRecord.index` + `build_comment_index`.

## Alternatives considered

1. **Named outputs (`outputs: [plan_doc]`, `{{ plan.plan_doc }}`)** — the v5
   design. Rejected: every name resolved to the same full comment, so the layer
   added cost with no semantics. It can return in the SaaS runner if typed
   fields ever matter.
2. **Prompt-only interpolation, no `inputs` seam** — rejected as the *sole*
   surface: it would discard the shipped typed/linted `render_inputs` seam
   (#813/#842) and duplicate bodies with escaping hazards.
3. **Bodies in state** — rejected: the state is re-embedded into every comment,
   so bodies-in-state compound against the comment size limit.
4. **Step-name references, one resolver, two surfaces** — *chosen*.

## Consequences

- Data flow between steps is declared, referenceable, and lint-checked
  (`L10-input-ref` resolves both reference shapes; new `L11-prompt-ref`
  validates `{{ <step> }}`).
- Backward compatible for every workflow that declares no outputs; migration is
  explicit (load-time errors), never silent.
- Resolution cost is O(1) per reference (a dict lookup) plus one bounded thread
  read already performed for the prompt.
- If a future engine wants typed fields, the wire format gains an optional
  `field` alongside `step` — an additive change to the locator.

## Worked example

`defaults/workflows/spec-driven-impl.yaml` (excerpt):

```yaml
  - name: plan                     # architect
    agent: architect
    next: stub
    # ...

  - name: implement                # dev
    agent: dev
    inputs: [plan]                 # ← the plan comment, materialized
    next: review

  - name: review
    agent: review
    inputs: [plan, implement]      # ← fan-in: plan + dev output
    prompt: |
      Review the implementation against the plan.
      Plan under review: {{ plan }}
      Both step outputs are also materialized above as deterministic inputs.
    conditions:
      - on_result: approved
        next: null
      - on_result: implement
        next: plan
```

At run time `review`'s prompt carries `## Step inputs (deterministic)` with
`#### \`plan\`` and `#### \`implement\`` (both full, state-stripped comments),
plus `{{ plan }}` substituted inline — all resolved by the engine before
`review` runs. This doubles as the #755 fan-in demonstration.
