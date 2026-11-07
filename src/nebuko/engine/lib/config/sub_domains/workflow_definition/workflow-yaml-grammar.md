---
title: "Workflow YAML grammar"
audience: user
sidebar_group: "Reference"
sidebar_label: "Workflow YAML grammar"
sidebar_order: 12
---

# Workflow YAML grammar

Workflows are first-class YAML definitions in `workflows/` (local) or fetched
from the nebuko repo (zero-copy). See [workflows.md](../../../../../../../docs/workflows.md). The
graph-level invariants below are checked offline by
[`python -m nebuko.engine lint`](../../../../../../../docs/lint.md).

| Key | Scope | Type | Required | Description |
|-----|-------|------|----------|-------------|
| `name` | file | string | yes | Workflow name (must match the filename). |
| `description` | file | string | no | Human-readable description. |
| `inputs` | file | list | no | Typed parameters this workflow requires — its function signature (issue #813). Each entry is a `name`/`kind`/`required` spec. See [workflows.md](../../../../../../../docs/workflows.md#typed-inputs-and-outputs). |
| `inputs[].name` | input | string | yes | Parameter name, referenced downstream through `input.<name>`. |
| `inputs[].kind` | input | `exec`\|`str` | yes | `exec` = a deterministic provider command materialized by the engine; `str` = a constant. |
| `inputs[].required` | input | bool | no | When true (default), a failed/absent value fails the consuming step deterministically, before the agent runs. |
| `outputs` | file | string[] | no | The workflow's results, each naming a **step** whose posted comment is that result (issue #756; a bare string coerces to a one-element list). Supersedes the pre-#756 opaque `<call-step>.<name>` return values. |
| `steps` | file | list | yes | Ordered list of steps. The first step is the workflow's entry point. |
| `steps[].name` | step | string | yes | Unique step name. |
| `steps[].agent` | step | string | one of `agent`/`workflow`/`repo`/`script` | Agent role executing the step. |
| `steps[].workflow` | step | string/path | one of `agent`/`workflow`/`repo`/`script` | Sub-workflow to mount instead of an agent (name or path like `tmp_impl/workflow.yaml`). |
| `steps[].script` | step | object | one of `agent`/`workflow`/`repo`/`script` | Deterministic, non-agent script execution (see below). |
| `steps[].script.command` | script | string | yes (with `script`) | Executable to run (`uv`, `git`, or a repo-relative script such as `scripts/test.sh`). Absolute paths and `..` segments are rejected. |
| `steps[].script.args` | script | string[] | no | Arguments passed to `command` (argv list, no shell interpolation). Entries survive to the child process verbatim, spaces and quotes included. |
| `steps[].script.working_dir` | script | string | no | Repo-relative working directory for the command; defaults to the checkout root. Absolute paths and `..` segments are rejected. |
| `steps[].script.env` | script | map<string,string> | no | Extra environment variables for the command; these override inherited variables with the same key. |
| `steps[].script.timeout_minutes` | script | int | no | Execution timeout in minutes (≥ 1; default 30, capped at 60). |
| `steps[].pre_hook` | step | object | no | Deterministic command run **in the session workspace immediately before the agent** (issue #884). Fail-closed: a non-zero exit or timeout skips the agent and fails the step. Agent steps only. |
| `steps[].post_hook` | step | object | no | Deterministic command run **in the session workspace immediately after the agent attempt** (issue #884), even when the agent fails. Fail-open: its failure is recorded but never changes the step outcome or routing. Agent steps only. |
| `steps[].pre_hook\|post_hook.path` | hook | string | yes (with the hook) | Repo-relative path of the script to run. Absolute paths and `..` segments are rejected. |
| `steps[].pre_hook\|post_hook.args` | hook | string[] | no | Arguments passed to the hook (argv list, no shell interpolation). |
| `steps[].pre_hook\|post_hook.timeout_minutes` | hook | int | no | Execution timeout in minutes (≥ 1; default 30, capped at 60). |
| `steps[].next` | step | string/null | no | Next step name, or `null` for terminal. |
| `steps[].conditions` | step | list | no | Conditional routing, evaluated in order. |
| `steps[].conditions[].on_result` | condition | string | yes | Result key reported by the agent (e.g., `approved`, `rejected`, `default`). |
| `steps[].conditions[].next` | condition | string/null | yes | Target step for that result. |
| `steps[].skill` | step | entry[] | no | Skill attachments for this step (files in `defaults/skills/`). Each entry is a bare name (legacy, means `inject`) or `- inject: [names]` / `- discover: [names]`; the step's mode wins over the agent's for a repeated name (issue #955; see [agents-and-skills.md](../../../../../../../docs/agents-and-skills.md#the-skills-system)). |
| `steps[].prompt` | step | string | no | Standing instruction injected into this step's agent context (multi-line YAML blocks supported). May interpolate upstream step comments with `{{ <step> }}` (issue #756). See [step-instructions.md](../workflow_step/step-instructions.md). |
| `steps[].complexity` | step | string | no | Declared complexity tier for this step's agent invocation: `low`, `medium`, or `high`. A dispatch `complexity` overrides it; otherwise it selects `agents.<name>.complexity_overrides.<tier>` and the tier temperature floor (issue #787; see [configuration.md](../../../../../../../docs/configuration.md#complexity-overrides-per-tier-configuration)). |
| `steps[].human_gate` | step | bool | no | Security boundary: the engine intercepts every advancing dispatch and holds the workflow until an identity-verified human releases it with `/nebuko approve gate` / `/nebuko reject gate` (see [human-gates.md](../../../resolve_config/features/resolve_route/human-gates.md)). |
| `steps[].inputs` | step | map or list | no | Call-site wiring (issue #813). On a `workflow` call step the keys are the callee's declared inputs; on an `agent` consumer step the values are `input.<name>` (a workflow parameter) or a bare `<step>` (that step's posted comment, issue #756). A list of step names (`inputs: [plan, implement]`) is shorthand for a map of `ref` bindings. Each value is one of `exec` (argv), `str` (constant), or a bare string meaning a `ref`. |

Minimal valid workflow:

```yaml
name: my-workflow
description: "A minimal two-step workflow"
trigger:
  agents: [architect]
steps:
  - name: analyze
    agent: architect
    next: review
  - name: review
    agent: review
    next: null
```

**Rules:**
- Step names must be unique within the workflow.
- `next` / `conditions[].next` must reference a valid step name or `null`.
- Conditions are evaluated in order; include a catch-all (e.g., `default`) to
  keep routing deterministic.
- A step must declare exactly one of `agent`, `workflow`, `repo`, or `script`.
- A `script` step is deterministic and non-agent: it runs `script.command`
  with `script.args` as argv (no shell), posts its output as a comment, then
  advances. Consecutive `script` steps run in one engine invocation over the
  same checkout, so state one step creates is visible to the next. The
  result is reported as `pass` (exit 0) or `fail` (non-zero exit / timeout) so
  `conditions[]` can route on it (e.g., `on_result: fail → dev`). **A failing
  script still advances** the workflow in v1 — it does not hold or terminate;
  the successor (usually an agent) receives the raw failure output.
- A workflow's declared `inputs`/`outputs` make it a typed function: every
  declared input must be bound **exactly once** at each `workflow` call site, a
  binding's kind must match the declaration (a `ref` is kind-preserving), and
  every reference must resolve in scope. `python -m nebuko.engine lint` checks this
  offline (rules [L8–L10](../../../../../../../docs/lint.md#rule-reference)).

Script step example:

```yaml
steps:
  - name: run-tests
    script:
      command: uv             # required (executable or repo-relative script)
      args: ["run", "pytest"]  # optional argv list
      working_dir: src         # optional, repo-relative
      env:                     # optional, overrides inherited keys
        CI: "true"
      timeout_minutes: 15      # optional, default 30
    next: review
```

Typed inputs example — a super-parent pushes concrete commands, a middle
workflow **forwards** them, and the leaf **consumes** them (block style is
canonical; a bare-string value is a `ref`):

```yaml
# super-parent.yaml — the composition root supplies the arguments
name: super-parent
steps:
  - name: spec-driven
    workflow: spec-driven
    inputs:
      setup_dev_env:
        exec: ["nix", "develop", "-c", "true"]
      run_test:
        exec: ["nix", "develop", "-c", "pytest", "."]
```

```yaml
# spec-driven.yaml — declares its parameters and forwards them unchanged
name: spec-driven
inputs:
  - name: setup_dev_env
    kind: exec
  - name: run_test
    kind: exec
outputs:
  - build
steps:
  - name: build
    workflow: spec-driven-impl
    inputs:
      setup_dev_env: input.setup_dev_env   # bare string ⇒ ref (kind-preserving)
      run_test: input.run_test
```

```yaml
# spec-driven-impl.yaml — the leaf consumes; `exec` runs just-in-time here
name: spec-driven-impl
inputs:
  - name: setup_dev_env
    kind: exec
  - name: run_test
    kind: exec
steps:
  - name: stub_and_implement
    agent: dev
    inputs:
      setup_dev_env: input.setup_dev_env
      run_test: input.run_test        # materialized immediately before the agent
```

Forwarding an `exec` input forwards the **command spec, not its output**: the
engine materializes it exactly once, at the deepest consuming step's
prompt-assembly seam, so freshness is depth-invariant. See
[ADR-0009](../../../../../../../docs/adr/0009-workflows-as-typed-functions.md).
