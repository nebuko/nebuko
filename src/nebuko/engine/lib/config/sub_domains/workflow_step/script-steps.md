---
title: "Script steps"
audience: user
sidebar_group: "Guides"
sidebar_label: "Script steps"
sidebar_order: 14
---

# Script steps

A step can be a **deterministic script run** instead of an agent: the engine
executes a command, posts its raw output as a comment, and advances to the next
step. The `script:` key is itself the step source (the nested object name is
the kind — no separate `kind` field), like `agent:` or `workflow:`. The fields
follow the Kubernetes container schema (`command` + `args`).

```yaml
steps:
  - name: run-tests
    script:
      command: uv               # required; executable or repo-relative script
      args: ["run", "pytest", "-q"]  # optional argv list (no shell interpolation)
      working_dir: src          # optional, repo-relative (default: repo root)
      env:                      # optional, overlaid on the engine environment
        PYTHONWARNINGS: error
      timeout_minutes: 15       # optional, >= 1 (default 30, capped at 60)
    next: review
```

There is no shell: `command` is executed directly with `args` as its argv
(`[command, *args]`), so metacharacters and quoting are never interpreted. Use
`command: sh` with `args: ["-c", "..."]` when you actually want a shell, and
remember the whole script is argv — an argument containing spaces or quotes
survives the round trip to the child process intact. `working_dir` is resolved
against the session checkout (absolute paths and `..` segments are rejected)
and `env` overrides inherited variables with the same key.

Mechanics:

1. The resolver detects the `script:` source and emits the execution contract
   (`RUN_SCRIPT=true`, `SKIP_AGENT=true`, `SCRIPT_PATH`, `SCRIPT_ARGS` as a
   JSON array, `SCRIPT_WORKING_DIR`, `SCRIPT_ENV` as a JSON object,
   `SCRIPT_TIMEOUT_MINUTES`, `WORKFLOW_STEP`) instead of a task assignment; no
   agent is run for that step. `SCRIPT_PATH` carries the `command` verbatim and
   `SCRIPT_ARGS` is decoded with `json.loads`, so argv round-trips losslessly.
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
   line** (command/args may carry secrets) — followed by a collapsed
   `<details><summary>command output</summary>` fenced block with the captured
   output (so trigger-like strings in the output stay inert). Oversized output
   is truncated head+tail with a marker to stay under GitHub's comment-size
   limit.
4. The completion comment is registered as the step output
   (`current_context.step_outputs` → `{step, comment_id}`), so a downstream
   `inputs: [<step>]` binds to it exactly like an agent answer (ADR-0013), and
   the `<!-- workflow-continue -->` token re-triggers the workflow gate so the
   **next** step runs — in its **own** engine invocation. The script's distinct
   `bot-command-*` tag is never counted as an agent turn by the circuit breaker.

   Exactly one script step runs per invocation (issue #994): the completion
   comment is the *only* thing that advances the workflow, so two independent
   advance mechanisms can never fire for the same edge. To chain several
   commands, put them in one step — `command: sh, args: ["-c", "a && b"]`.
   Cross-step state does not survive, so a step must not depend on a file, ref
   or virtualenv an earlier step produced.

Result semantics (feeds the existing `conditions` mechanism):

- Exit `0` → result `pass`; non-zero exit or timeout → result `fail`.
- If the step declares `conditions[]`, routing uses the matching branch
  (e.g. `on_result: fail → dev`); otherwise it follows the declared `next`.
- **A failing script still advances the workflow** in v1 — it does not hold
  or terminate. The successor (usually an agent) receives the raw failure
  output and reacts. A `stop_on_fail` policy switch is a deferred
  enhancement.

Security notes:

- A repo-relative `command`/`working_dir` is re-verified at runtime (realpath
  containment inside the checkout); absolute paths and `..` segments are
  rejected at parse time.
- The command line (`command` + `args`) is deliberately **not echoed** in the
  posted comments; only the step name, exit code and duration appear in the
  banner. The captured *output* is posted as-is — the same trust level as an
  agent answer.
- The script runs in the same CI job with the job's full environment and
  permissions — the same trust level as the workflow YAML itself, plus the
  step's own `env`. Secret scrubbing / sandboxed execution are deferred
  enhancements.
