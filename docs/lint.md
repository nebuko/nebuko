# Linting workflows

`python -m engine lint` validates workflow YAML for **graph-level** defects —
the problems that only appear once the whole step graph is assembled and that
the per-field schema loader cannot see. It is offline and read-only: it never
runs an agent, mutates a workflow, or touches GitHub.

| | |
|---|---|
| **Entry point** | `python -m engine lint <paths…>` |
| **Exit codes** | `0` clean (or warnings only) · `1` at least one error · `2` usage/load failure |
| **Rule set** | `L1`–`L7` (see [Rule reference](#rule-reference)) |
| **Source** | `engine/lint.py` (rules) + `engine/lint_cli.py` (command) |

---

## Why a separate linter

The Pydantic loader under `engine/lib/config/features/load_workflow`
enforces every workflow's **field-level** shape (types, required keys, enums).
Its rules are local to one key at a time, so it cannot detect structural
defects that span the graph:

- `next` pointing at a step that does not exist,
- steps no route ever reaches,
- condition keys the resolver can never select,
- a `human_gate` step that can never be released,
- a graph with no terminal step at all.

These surface today only at runtime, mid-workflow. The linter catches them in
CI, before a run starts. It **reuses the runtime resolver's traversal helpers**
(`match_condition`, `branch_key_for`, `is_terminal_step` in
`engine/lib/resolve_workflow.py`) rather than re-deriving the semantics, so
the lint verdict and the runtime behaviour can never drift apart.

---

## Usage

```bash
# one file
python -m engine lint workflows/spec-driven-impl.yaml

# every *.yaml / *.yml in a directory
python -m engine lint workflows/

# promote warnings to errors (and flip the exit code)
python -m engine lint --strict workflows/

# machine-readable output
python -m engine lint --format json workflows/
```

### Text output

One line per issue, followed by a summary:

```
$ python -m engine lint workflows/
0 issue(s): 0 error(s), 0 warning(s)
```

```
$ python -m engine lint tests/engine/lint_fixtures/l1_dangling_next.yaml
[L1-dangling-next] error step=analyze step 'analyze' routes to unknown step 'ghost' (tests/engine/lint_fixtures/l1_dangling_next.yaml:6)
[L2-unreachable-step] warning step=review step 'review' is unreachable from the entry step 'analyze' (tests/engine/lint_fixtures/l1_dangling_next.yaml:9)
2 issue(s): 1 error(s), 1 warning(s)
```

Each line is `[<rule>] <severity> step=<name> <message> (<file>:<line>)`. The
`location` is best-effort — the line number of the offending step in the YAML
source; the rule message and `hint` never depend on it.

### JSON output

`--format json` serializes the full `LintIssue` list (rule, severity, message,
step, location, hint), for editors and dashboards:

```json
[
  {
    "rule": "L1-dangling-next",
    "severity": "error",
    "message": "step 'a' routes to unknown step 'ghost'",
    "step": "a",
    "location": "tests/engine/lint_fixtures/multi_error.yaml:6",
    "hint": "Declare the missing step or correct the target"
  }
]
```

### Strict mode

Rule **L2** is a *warning* by default, so a workflow with only unreachable steps
exits `0`. `--strict` promotes every warning to `error` — pass it in CI when you
want an unreachable step to fail the build.

---

## Exit codes

| Code | Meaning | When |
|------|---------|------|
| `0` | Clean | No issues, or warnings only without `--strict`. |
| `1` | Errors | At least one `error`-severity issue (warnings promoted count in `--strict`). |
| `2` | Usage/load failure | Bad arguments, a path that does not exist, a directory with no `*.yaml`, or a file the schema loader rejects. Load failures are **not** reported as `LintIssue`s — they are command failures. |

---

## Rule reference

| Rule | Severity | Detects |
|------|----------|---------|
| `L1-dangling-next` | error | `next` / `conditions[].next` names a step that does not exist. |
| `L2-unreachable-step` | warning → error under `--strict` | A step no path from the entry step reaches. |
| `L3-duplicate-step-id` | error | Two steps share the same `name`. |
| `L4-condition-key` | error | A step declares the same `on_result` key more than once; only the first match is reachable. |
| `L5-terminal-consistency` | error | A non-terminal entry step that routes only through `conditions` and has no linear `next`, so a branch-less dispatch dead-ends. |
| `L6-human-gate` | error | A `human_gate: true` step that is terminal; its hold can never resume. |
| `L7-no-terminal` | error | No step can terminate — every path cycles. |

### L1 — dangling `next`

A routing target that is not a declared step. The workflow dead-ends the moment
that route fires.

```yaml
steps:
  - name: analyze
    agent: genius
    next: ghost      # ← no step named 'ghost'
  - name: review
    agent: review
    next: null
```

**Fix:** declare the missing step or correct the target.

### L2 — unreachable step

A step that no route from the entry step (the first step in `steps`) can reach.
It is dead weight — often a leftover after a route was removed.

```yaml
steps:
  - name: analyze
    agent: genius
    next: review
  - name: review
    agent: review
    next: null
  - name: orphan     # ← nothing routes here
    agent: dev
    next: null
```

**Fix:** route to the step, or delete it. In CI, add `--strict` to make this
fail the build.

### L3 — duplicate step id

Two steps declared with the same `name`. Step names are the routing keys, so a
duplicate makes `next` ambiguous.

```yaml
steps:
  - name: dup
    agent: genius
    next: done
  - name: done
    agent: dev
    next: null
  - name: dup        # ← 'dup' already declared
    agent: review
    next: null
```

**Fix:** give every step a unique name.

### L4 — dead / duplicate condition key

`match_condition` returns the **first** condition whose `on_result` matches, so a
repeated `on_result` on the same step is unreachable by construction.

```yaml
steps:
  - name: gate
    agent: genius
    next: done
    conditions:
      - on_result: approved   # ← selected
        next: done
      - on_result: approved   # ← never selectable
        next: done
```

**Fix:** remove the unreachable duplicate, or merge the two branches.

### L5 — terminal consistency

The resolver falls back to a step's linear `next` on a branch-less dispatch. A
non-terminal **entry** step that routes only through `conditions` has no such
fallback, so an agent that emits no matching branch dead-ends the workflow
immediately. (Downstream conditional steps are always entered from a routed
lookup, so they are not flagged.)

```yaml
steps:
  - name: analyze        # ← entry step, no linear 'next'
    agent: genius
    conditions:
      - on_result: approved
        next: done
  - name: done
    agent: dev
    next: null
```

**Fix:** add a `next` fallback route to the entry step.

### L6 — unresumable human gate

A `human_gate: true` step with no outgoing route can never be released: the
engine holds the workflow waiting for `/workflow resume`, but there is nowhere
for it to go. See [workflows.md](./workflows.md) for gate/resume semantics.

```yaml
steps:
  - name: analyze
    agent: genius
    next: gate
  - name: gate
    agent: genius
    human_gate: true
    next: null           # ← terminal gate, cannot resume
```

**Fix:** give the gate a `next` or condition route to release it.

### L7 — no terminal step

Every route in the graph leads back into the graph — there is no step that ends
the workflow, so any run loops forever. A step counts as terminating if it is
terminal (`next: null`) **or** if a branch of its `conditions` has
`next: null`.

```yaml
steps:
  - name: a
    agent: genius
    next: b
  - name: b
    agent: dev
    next: a        # ← a ↔ b, no way out
```

**Fix:** add a step with `next: null` (directly, or as a conditional branch).

---

## Human gates

Rule **L6** is the linter's contribution to the gate contract described in
[workflows.md](./workflows.md#human-gates): a `human_gate: true` step pauses the
workflow until a human releases it with `/workflow resume`. That release
re-dispatches the step with the human's decision as the result key, so the step
*must* have somewhere to go. A terminal gate is therefore always an error — the
workflow can be paused but never continued.

A gate is valid when it declares either a linear `next` or `conditions` that
cover the resume decision:

```yaml
  - name: approve
    agent: genius
    human_gate: true
    conditions:
      - on_result: approved
        next: implement
      - on_result: rejected
        next: plan
```

---

## CI integration

`.github/workflows/lint-workflows.yaml` runs the linter on every pull request
that touches `workflows/`, `engine/`, or the workflow schema:

```yaml
- name: Lint workflow YAML
  run: python -m engine lint workflows/
```

Add `--strict` to treat warnings (L2) as failures, or `--format json` to feed
the issues into another tool. Exit code `1` fails the job; exit code `2` means
the linter could not load a file (bad path or schema-invalid YAML).

---

## Relationship to the schema loader

The linter is a **consumer** of the loader, not a replacement. Loading still
happens exactly as at runtime (`config.load_workflow`); the linter then walks the
loaded graph. If a file is rejected by the schema, the linter reports it as a
load failure (exit `2`) rather than a `LintIssue` — schema validity is a
precondition for graph-level analysis, not one of its rules.
