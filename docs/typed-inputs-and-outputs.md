---
title: "Typed inputs and step outputs"
audience: user
covers:
  - src/nebuko/engine/input_wiring.py
sidebar_group: "Guides"
sidebar_label: "Typed inputs and outputs"
sidebar_order: 16
---

# Typed inputs and step outputs

A workflow is a **typed function** (issue #813): it declares `inputs` (its
parameters) and `outputs` (its return values), and the caller supplies concrete
values at each `workflow` call site. This is how the engine guarantees a step
receives the artifacts it needs — the plan, a `git diff`, test results —
**deterministically**, without the model spending turns fetching them or
hallucinating their absence (see [ADR-0009](adr/0009-workflows-as-typed-functions.md)).

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

Wiring is checked offline by [`python -m nebuko.engine lint`](lint.md) (rules
[L8–L11](lint.md#rule-reference)): every declared input bound exactly once per
call site, kinds matched, references resolvable in scope, every declared
input consumed or forwarded, and every prompt reference naming a declared step.

## Step Outputs — A Step's Output Is Its Comment

Data flow **between steps of the same workflow** needs no declaration: a step's
output *is* the comment it posts, addressed by the **step name** (issue #756,
[ADR-0013](adr/0013-step-outputs-are-step-comments.md)). A downstream step
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
references; a leftover `steps[].outputs:` key fails at load time with a #756
migration hint. The dotted form is valid again **only** for a field a step
declares in its `output:` block (issue #973, below): `{{ plan.headline }}` and
`inputs: {summary: plan.headline}` resolve to that one section, while a dotted
ref to an undeclared field is reported by the linter (`L10-input-ref`,
`L11-prompt-ref`).

## Declared Step Output — The `output:` Shape (issue #973)

A step may additionally declare the **shape of its own posted comment** with an
`output:` block. This is opt-in: a step without `output:` keeps the legacy
`finish` / `dispatch_next` terminal contract byte-for-byte.

```yaml
steps:
  - name: implement
    agent: dev
    next: review
    output:
      fields:
        - name: headline            # the comment's `#` lead-in
          title: true
        - name: changes             # a `## Changes` section (required)
          description: What changed, with file paths.
        - name: verification        # optional: absent ⇒ no heading, no markers
          required: false
        - name: matrix              # a table field
          type: table
          columns: [case, result]
```

Field keys:

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `name` | `^[a-z][a-z0-9_]*$` | — | Section id; also the marker name. |
| `type` | `text` \| `table` | `text` | A `table` field requires `columns`. |
| `description` | string | `""` | Shown in the prompt block's field list. |
| `heading` | string | capitalized `name` | The rendered `##` heading. |
| `title` | bool | `false` | At most one `text` field; its body is the `#` lead-in. |
| `collapsed` | bool | `false` | Wrap the body in `<details>`. |
| `required` | bool | `true` | A missing required section fails the turn. |
| `columns` | list | `[]` | Column names for a `table` field. |

**Terminal contract.** For a step that declares `output:`, the engine appends
the section prompt block to the step prompt and exposes the terminal `finalize`
tool (with `call_agent` for delegation; `finish`/`dispatch_next` are hidden).
The turn carries exactly two artifacts:

- the **report** — the turn's markdown text, written as section bodies between
  the exact markers the prompt block shows; and
- the **route** — the `finalize` tool's typed `route` argument
  (`status`/`agent`/`complexity`/`branch`/`pin`/`reason`), exposed to the model
  as the tool's `response_schema` so a long markdown payload can never truncate
  the routing decision.

The engine — never the model — owns the markdown wrappers (`#` title, `##`
headings, `<details>`), extracts the bodies, validates them (known names,
required present, no `<!--` injection) and renders the posted comment. A
malformed answer gets exactly one retry naming the offending field; a second
failure salvages the completed sections and logs loudly. A truncated turn is
salvaged the same way and routes `hold`.

See [`.nebuko/workflows/default-with-output.yaml`](../.nebuko/workflows/default-with-output.yaml)
for a runnable copy of `default` that declares `output:` on its report-posting
steps (`analyze`, `plan`, `implement`, `review`); `context` stays undeclared
because its comment is the free-form data payload downstream steps consume.
