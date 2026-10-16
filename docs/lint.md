# Linting workflows

`python -m nebuko.engine lint` validates workflow YAML for **graph-level** defects —
the problems that only appear once the whole step graph is assembled and that
the per-field schema loader cannot see. It is offline and read-only: it never
runs an agent, mutates a workflow, or touches GitHub.

| | |
|---|---|
| **Entry point** | `python -m nebuko.engine lint <paths…>` |
| **Exit codes** | `0` clean (or warnings only) · `1` at least one error · `2` usage/load failure |
| **Rule set** | `L1`–`L11` (see [Rule reference](#rule-reference)) |
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
python -m nebuko.engine lint defaults/workflows/spec-driven-impl.yaml

# every *.yaml / *.yml in a directory
python -m nebuko.engine lint defaults/workflows/

# promote warnings to errors (and flip the exit code)
python -m nebuko.engine lint --strict defaults/workflows/

# machine-readable output
python -m nebuko.engine lint --format json defaults/workflows/
```

### Text output

One line per issue, followed by a summary:

```
$ python -m nebuko.engine lint defaults/workflows/
0 issue(s): 0 error(s), 0 warning(s)
```

```
$ python -m nebuko.engine lint tests/engine/lint_fixtures/l1_dangling_next.yaml
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
| `L8-input-binding` | error | A call-site binding names an input the callee does not declare, or leaves a `required` input unbound. |
| `L9-input-kind` | error | A call-site binding's kind does not match the callee's declared kind — a `ref` must be kind-preserving. |
| `L10-input-ref` | warning → error under `--strict` | A consumer references an `input.<name>` or a bare `<step>` not in scope, a step is named `input` (reserved), or a declared input is never consumed or forwarded. |
| `L11-prompt-ref` | warning → error under `--strict` | A step `prompt` contains a `{{ <step> }}` placeholder that does not name a declared step. |

### L1 — dangling `next`

A routing target that is not a declared step. The workflow dead-ends the moment
that route fires.

```yaml
steps:
  - name: analyze
    agent: architect
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
    agent: architect
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
    agent: architect
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
    agent: architect
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
    agent: architect
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
    agent: architect
    next: gate
  - name: gate
    agent: architect
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
    agent: architect
    next: b
  - name: b
    agent: dev
    next: a        # ← a ↔ b, no way out
```

**Fix:** add a step with `next: null` (directly, or as a conditional branch).

### L8 — call-site binding mismatch

A workflow is a typed function: a `workflow` call step's `inputs` are its
arguments. L8 fires when a binding names an input the callee does not declare,
or when a `required` input is left unbound. A callee that the linter cannot
resolve (missing file, bad path) is skipped here — that is L1's concern.

```yaml
# build calls spec-driven-impl, which declares only 'run_test'
- name: build
  workflow: spec-driven-impl
  inputs:
    setup_dev_env: ["nix", "develop"]   # ← callee declares no such input
    # 'run_test' is required and never bound
```

**Fix:** bind exactly the declared inputs; remove stale bindings.

### L9 — binding kind mismatch

The bound value's kind must equal the callee's declared kind. A `ref` is
**kind-preserving**: a `ref` to an `exec` input forwards a command spec, not a
string, so a `str`-declared target cannot receive it (and vice-versa).

```yaml
# callee declares run_test: {kind: exec}; the caller passes a constant
inputs:
  run_test: "pytest ."        # ← str binding for an exec input
```

**Fix:** match the declared kind — usually by forwarding `input.<name>`.

### L10 — unresolvable or unused reference

Two shapes of the same "not wired" defect. First, a consumer `agent` step
references `input.<name>` (a workflow parameter) or a bare `<step>` (an upstream
step whose posted comment is the value; ADR-0013) that is not in scope. A step
named `input` is flagged too: `input.` is a reserved namespace. Second, a
declared input is never consumed or forwarded by any step (even via a nested
call), so the parameter is dead. A file-level `outputs` entry must name a
declared step � a workflow's results are the comments of the steps it names.

```yaml
# 'run_test' is declared but no step ever references input.run_test
inputs:
  - {name: run_test, kind: exec}
```

**Fix:** reference the value from a step, or drop the declaration.

### L11 — unresolved prompt reference

A `steps[].prompt` may interpolate upstream step comments with
`{{ <step> }}`. L11 fires when a placeholder names something that is not a
declared step — at run time it would degrade to `_(unavailable: <step>)_`.

```yaml
steps:
  - name: implement
    agent: dev
    next: null

  - name: review
    agent: review
    prompt: "Review against {{ plan }}"   # ← no step named 'plan'
    next: null
```

**Fix:** reference a declared step name, or remove the placeholder. (A step's
output is its posted comment; see [ADR-0013](./adr/0013-step-outputs-are-step-comments.md).)

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
    agent: architect
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
  run: python -m nebuko.engine lint defaults/workflows/
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

---

# Linting the agent config

`python -m nebuko.engine lint-config` is the sibling linter for the layered
**agent config** (`providers` → `llms` → `agents`, see
[configuration.md](./configuration.md)). Like the workflow linter it is offline
and read-only: it never resolves a reference against a live model, imports the
SDK runtime, or touches GitHub.

| | |
|---|---|
| **Entry point** | `python -m nebuko.engine lint-config [paths…]` |
| **Exit codes** | `0` clean (or warnings only) · `1` at least one error · `2` usage/load failure |
| **Rule set** | `C1`–`C6` (see [Agent-config rule reference](#agent-config-rule-reference)) |
| **Source** | `src/nebuko/engine/config_lint.py` (rules) + `src/nebuko/engine/config_lint_cli.py` (command) |

> The workflow linter predates the `src/nebuko/` layout (issue #741). Under the
> current layout both commands run from the package — `python -m nebuko.engine
> lint …` and `python -m nebuko.engine lint-config …`.

---

## Why a separate linter

The Pydantic loader (`engine/lib/config/features/load_config`) enforces every
key's **field-level** shape — types, required keys, enums. But it runs *after*
`${layer.key}` resolution, so the defects the linter targets are invisible to it:

- a dangling reference is raised as a bare `KeyError` by `resolve_refs` before
  the loader ever validates the mapping, so the run dies with no hint of which
  key is wrong;
- a tool name the runtime does not know is silently skipped with a log line
  (`run_agent.py`), so the agent simply loses that capability;
- a `prompt_file` or skill that does not exist is only noticed once that agent
  is dispatched, mid-run;
- an `llms`/`providers` entry nothing references is dead config that quietly
  accumulates.

The linter reports all of them offline, and reports **every** finding in one run
rather than stopping at the first.

---

## Usage

```bash
# the shipped agent config (layered: .nebuko/agents/ → defaults/agents/)
python -m nebuko.engine lint-config

# one file …
python -m nebuko.engine lint-config defaults/agents/agent-config.yaml

# … or every *.yaml / *.yml in a directory
python -m nebuko.engine lint-config defaults/agents/

# promote warnings to errors (and flip the exit code)
python -m nebuko.engine lint-config --strict defaults/agents/

# machine-readable output
python -m nebuko.engine lint-config --format json defaults/agents/
```

With no path, the command lints the config the runtime itself would load. File
references are checked against the same layered lookup the engine uses, so a
shipped config is never falsely flagged for a prompt or skill that resolves
under `defaults/`.

### Text output

One line per issue and a summary. Config issues have no step, so the trailing
parenthesised field is the dotted **config key path**:

```
$ python -m nebuko.engine lint-config defaults/agents/
[C3-unknown-tool] error unknown tool 'warp_drive' (agents.dev.tools)
[C6-unreferenced-entry] warning llm 'l_unused' is never referenced by any agent (llms.l_unused)
[C6-unreferenced-entry] warning provider 'p_unused' is never referenced by any agent (providers.p_unused)
3 issue(s): 1 error(s), 2 warning(s)
```

### JSON output

`--format json` prints a single JSON array of issues and nothing else, so it can
be piped straight into another tool:

```json
[
  {
    "rule": "C6-unreferenced-entry",
    "severity": "warning",
    "message": "provider 'local_vllm' is never referenced by any agent",
    "step": "",
    "location": "providers.local_vllm",
    "hint": "Remove the unused entry or reference it"
  }
]
```

### Strict mode

`--strict` promotes every warning (`C6`) to an error, which also flips the exit
code. Use it to make dead config fail CI:

```bash
python -m nebuko.engine lint-config --strict defaults/agents/
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | No errors, and no warnings under `--strict`. |
| `1` | At least one issue has severity `error` under the current mode. |
| `2` | Usage or load failure: a path does not exist, the file is not valid YAML, its root is not a mapping, or no config files were found. |

The mapping is uniform — any `error`-severity issue (including a promoted `C6`
warning) yields `1`, and warnings alone yield `0` — so a CI consumer never has
to guess at the exit code.

---

## Agent-config rule reference

Each rule is identified by a stable `C`-prefixed code and reports every finding
it sees, in rule order (`C1` … `C6`).

| Rule | Severity | Catches |
|---|---|---|
| `C1-schema` | error | Violations of the `AppConfig` schema, all of them at once |
| `C2-dangling-ref` | error | `${layer.key}` pointing at a nonexistent entry |
| `C3-unknown-tool` | error | `tools` entries outside the runtime's known set |
| `C4-prompt-file` | error | `prompt_file` that resolves to no file on disk |
| `C5-skill-file` | error | `skills` entry with no matching skill file |
| `C6-unreferenced-entry` | warning | `llms`/`providers` entry nothing references |

### `C1-schema` — schema violations

The config is resolved (best-effort) and validated against `AppConfig`. Pydantic
collects *all* violations, so a config with several mistakes reports all of them
in one run:

```yaml
agents:
  dev:
    model: "${llms.qwen}"
    temperature: hot        # ← not a number
                            # ← and prompt_file is missing entirely
```

```
[C1-schema] error Input should be a valid number, unable to parse string as a number (agents.dev.temperature)
[C1-schema] error Field required (agents.dev.prompt_file)
```

### `C2-dangling-ref` — undefined references

Every `${layer.key}` must resolve against the config itself. The linter walks the
**raw** mapping (it deliberately does not call `resolve_refs`, which would raise
`KeyError` and hide the location):

```yaml
agents:
  dev:
    model: "${llms.qwen-typo}"   # no such entry under `llms:`
```

```
[C2-dangling-ref] error undefined ref ${llms.qwen-typo} (agents.dev.model)
```

A dangling reference is also reported by `C1-schema`, because the value cannot
satisfy the schema once the broken ref is substituted — fix the reference and
both lines disappear.

### `C3-unknown-tool` — unknown tool names

`tools` entries are checked against the canonical set the runtime accepts
(`terminal`, `file_editor`, `task_tracker`, `think`, `finish`, `dispatch_next`,
`report_scaffold_branch`, per `agent_script/features/run_agent`). The runtime
merely logs and skips an unknown tool, so the agent silently loses it:

```yaml
agents:
  dev:
    tools: ["think", "finish", "warp_drive"]   # ← no such tool
```

```
[C3-unknown-tool] error unknown tool 'warp_drive' (agents.dev.tools)
```

### `C4-prompt-file` — missing prompt file

Every `prompt_file` must resolve to a file — an explicit path relative to the
config, or a name found through the layered prompt lookup
(`.nebuko/prompts/` → `defaults/prompts/`). URLs are accepted, since the runtime
prompt loader supports them:

```yaml
agents:
  dev:
    prompt_file: defaults/prompts/devv.md   # ← typo, no such file
```

```
[C4-prompt-file] error prompt file 'defaults/prompts/devv.md' does not exist (agents.dev.prompt_file)
```

### `C5-skill-file` — missing skill file

Every `skills` entry must resolve — a bare name to `defaults/skills/{name}.md`, an
explicit path as written, either also found through the layered skill lookup.
Names inside the mode-object shape (issue #955) are checked the same way:

```yaml
agents:
  dev:
    skills:
      - inject: ["call-agent"]
      - discover: ["posting-contrat"]   # ← typo, no such skill
```

```
[C5-skill-file] error missing skill file for 'posting-contrat' (agents.dev.skills)
```

### `C6-unreferenced-entry` — dead config

An `llms` or `providers` entry that no `${ref}` anywhere reaches is unreachable
config. This is the one *warning*: a deliberately staged entry is legitimate, so
it does not fail the build unless `--strict` is given.

```yaml
providers:
  openrouter:
    api_url: "https://openrouter.ai/api/v1"
    api_token_env: "OPENROUTER_API_KEY"
  retired:
    api_url: "https://retired.example/v1"
    api_token_env: "RETIRED_API_KEY"   # ← referenced by nothing
```

```
[C6-unreferenced-entry] warning provider 'retired' is never referenced by any agent (providers.retired)
```

---

## CI integration

`.github/workflows/lint-agent-config.yaml` runs the linter on every pull request
that touches the shipped agent configs or the linter itself:

```yaml
- name: Lint agent-config YAML
  env:
    PYTHONPATH: src
  run: python -m nebuko.engine lint-config defaults/agents/
```

The default agent configs intentionally carry `C6` warnings (staged models), so
the CI run keeps the default, non-strict mode and exits `0`. Add `--strict` to
gate on dead config too, or `--format json` to feed the issues into another
tool. Exit code `1` fails the job; exit code `2` means the linter could not load
a file (bad path, invalid YAML, or a non-mapping root).

---

## Relationship to the config loader

The linter is a **consumer** of the same grammar, not a replacement. It reuses
the config package's `REF_PATTERN` so reference *syntax* cannot drift, and the
`LintIssue` type is shared with the workflow linter so both read identically.
Loading at run time still happens exactly as before (`config.load_config`); the
linter only reports what that load would do — or fail to do — ahead of time.
