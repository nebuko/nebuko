# Reference

Machine-readable contracts used by nebuko: the `AGENT_RUN_CONFIG` dispatch
sentinel, the workflow YAML grammar, the `WORKFLOW_STATE` v1 JSON schema, the
environment-variable catalog, and the bot marker conventions.

---

## AGENT_RUN_CONFIG

`AGENT_RUN_CONFIG` is a single-line HTML-comment sentinel that carries a
compact JSON payload. It is a durable, **engine-rendered record** of a run's
hand-off: the engine builds it from typed state (the `dispatch_next` /
`call_agent` control-plane tools) and appends it to the posted comment exactly
once. Agents do **not** emit it themselves (issue #876); the parser below is
retained only as a compatibility input for legacy comments. The canonical
matching pattern is defined in `engine/lib/validate_output.py`; all consumers
(output building, comment preparation) reuse it from there.

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "medium"} -->
```

Single line + HTML comment means no multi-line boundary ambiguity and no leak
into rendered GitHub comments. A sentinel split across lines never matches.

### Canonical Contract (`AgentRunConfig`)

The payload is validated by the canonical model `AgentRunConfig`
(`engine/lib/config/sub_domains/agent_run_config/agent_run_config.py`),
shared by the validator and the routing helpers. Two invariants hold:

1. **At least one action is required** — one of `task`, `dispatch` or
   `function_call`. An empty payload is invalid.
2. **`task` and `function_call` are mutually exclusive** — they are two
   different ways to name a target; combining them is rejected.

`task` is therefore **not** required: a dispatch-only sentinel such as
`{"dispatch": {"status": "advance"}}` is valid. See
[handoff-and-callback.md](./handoff-and-callback.md) for the full contract,
the `callback` return address, and the routing-precedence cascade.

### Dispatch Object

The current dispatch contract (see
`engine/lib/workflow_state/sub_domains/dispatch/dispatch.py`):

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Agent outcome: `advance`, `loop`, `hold`, or `terminate`. The workflow engine resolves routing from this plus the workflow definition. |
| `task_hint` | string | Optional suggested next agent (overrides workflow YAML). |
| `complexity` | string | Complexity for the downstream agent: `low`, `medium`, `high`. When unset, the downstream workflow step's declared `steps[].complexity` applies, else `medium` (issue #787). Auto-adjusts temperature (see [configuration.md](./configuration.md)). |
| `reasoning` | string | Reasoning effort: `none`, `low`, `medium`, `xhigh` (`high` is an alias for `xhigh`). Only applies while thinking is enabled in the LLM config. |
| `model` | string | Optional LLM model override for the downstream agent. |
| `temperature` | float | Sampling override. |
| `top_p` | float | Nucleus sampling override. |
| `top_k` | int | Top-K selection override. |
| `min_p` | float | Minimum-P sampling override. |
| `presence_penalty` | float | Presence penalty override. |
| `repetition_penalty` | float | Repetition penalty override. |
| `workflow` | string | Optional workflow name override (file in `workflows/`). |
| `step` | string | Optional step name override within the workflow. |
| `branch` | string | Optional condition key for conditional routing. |
| `parent_workflow` | string | Parent workflow name when entering a sub-workflow. |
| `parent_workflow_step` | string | Parent step that triggered the sub-workflow. |
| `skills` | string | Optional skill(s) for the downstream agent. |
| `pin` | bool | When true, pin this step's output comment(s) so downstream agents receive their full body verbatim (mirrors the `PIN_COMMENT` workflow input / env var). |
| `reason` | string | Optional free-text explanation of the dispatch decision. |

Defaults: `status: "advance"`, `reasoning: "none"`, everything else
empty/null. An unset `complexity` falls back to the workflow step's declared
`steps[].complexity`, else `"medium"` (issue #787).

### Legacy Flat Fields (Backward Compatibility)

Older sentinels use flat fields instead of `dispatch` (see the README schema
table): `task` (the target agent role; required only when neither `dispatch`
nor `function_call` is present), `skills` (string[]),
`complexity`, `reasoning`, `round` (dev↔review callback counter),
`temperature`, `top_p`, `top_k`, `repetition_penalty`, `model`, `workflow`,
`step`, `branch`.

Examples:

```
<!-- AGENT_RUN_CONFIG {"task": "review", "complexity": "medium"} -->
<!-- AGENT_RUN_CONFIG {"task": "dev", "round": 2} -->
<!-- AGENT_RUN_CONFIG {"task": "context", "workflow": "default", "step": "context"} -->
<!-- AGENT_RUN_CONFIG {"task": "dev", "complexity": "low", "top_p": 0.1, "top_k": 10, "repetition_penalty": 1.2} -->
```

**Rule:** human users trigger agents with `@@mention`; the engine renders
`AGENT_RUN_CONFIG` from typed state (`dispatch_next` / `call_agent`) — agents no
longer emit it themselves (issue #876). Bot agents cannot use `@@mention` to
avoid self-triggering loops.

---

## Workflow YAML Grammar

Workflows are first-class YAML definitions in `workflows/` (local) or fetched
from the nebuko repo (zero-copy). See [workflows.md](./workflows.md). The
graph-level invariants below are checked offline by
[`python -m engine lint`](./lint.md).

| Key | Scope | Type | Required | Description |
|-----|-------|------|----------|-------------|
| `name` | file | string | yes | Workflow name (must match the filename). |
| `description` | file | string | no | Human-readable description. |
| `inputs` | file | list | no | Typed parameters this workflow requires — its function signature (issue #813). Each entry is a `name`/`kind`/`required` spec. See [workflows.md](./workflows.md#typed-inputs-and-outputs). |
| `inputs[].name` | input | string | yes | Parameter name, referenced downstream through `input.<name>`. |
| `inputs[].kind` | input | `exec`\|`str` | yes | `exec` = a deterministic provider command materialized by the engine; `str` = a constant. |
| `inputs[].required` | input | bool | no | When true (default), a failed/absent value fails the consuming step deterministically, before the agent runs. |
| `outputs` | file | string[] | no | The workflow's results, each naming a **step** whose posted comment is that result (issue #756; a bare string coerces to a one-element list). Supersedes the pre-#756 opaque `<call-step>.<name>` return values. |
| `trigger.agents` | file | string[] | no | Which `@@mention`s can start this workflow. |
| `steps` | file | list | yes | Ordered list of steps. |
| `steps[].name` | step | string | yes | Unique step name. |
| `steps[].agent` | step | string | one of `agent`/`workflow`/`repo`/`script` | Agent role executing the step. |
| `steps[].workflow` | step | string/path | one of `agent`/`workflow`/`repo`/`script` | Sub-workflow to mount instead of an agent (name or path like `tmp_impl/workflow.yaml`). |
| `steps[].script` | step | object | one of `agent`/`workflow`/`repo`/`script` | Deterministic, non-agent script execution (see below). |
| `steps[].script.path` | script | string | yes (with `script`) | Repo-relative path of the script to run. Absolute paths and `..` segments are rejected. |
| `steps[].script.args` | script | string[] | no | Arguments passed to the script (argv list, no shell interpolation). |
| `steps[].script.timeout_minutes` | script | int | no | Execution timeout in minutes (≥ 1; default 30, capped at 60). |
| `steps[].next` | step | string/null | no | Next step name, or `null` for terminal. |
| `steps[].conditions` | step | list | no | Conditional routing, evaluated in order. |
| `steps[].conditions[].on_result` | condition | string | yes | Result key reported by the agent (e.g., `approved`, `rejected`, `default`). |
| `steps[].conditions[].next` | condition | string/null | yes | Target step for that result. |
| `steps[].skill` | step | string[] | no | Skills attached to the step prompt (files in `defaults/skills/`). |
| `steps[].prompt` | step | string | no | Standing instruction injected into this step's agent context (multi-line YAML blocks supported). May interpolate upstream step comments with `{{ <step> }}` (issue #756). See [workflows.md](./workflows.md#step-instructions-prompt). |
| `steps[].complexity` | step | string | no | Declared complexity tier for this step's agent invocation: `low`, `medium`, or `high`. A dispatch `complexity` overrides it; otherwise it selects `agents.<name>.complexity_overrides.<tier>` and the tier temperature floor (issue #787; see [configuration.md](./configuration.md#complexity-overrides-per-tier-configuration)). |
| `steps[].human_gate` | step | bool | no | Security boundary: the engine intercepts every advancing dispatch and holds the workflow until an identity-verified human releases it with `/nebuko approve gate` / `/nebuko reject gate` (see [workflows.md](./workflows.md#human-gates)). |
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
- A `script` step is deterministic and non-agent: it runs the repo-local script
  named by `script.path`, posts its output as a comment, then advances. The
  result is reported as `pass` (exit 0) or `fail` (non-zero exit / timeout) so
  `conditions[]` can route on it (e.g., `on_result: fail → dev`). **A failing
  script still advances** the workflow in v1 — it does not hold or terminate;
  the successor (usually an agent) receives the raw failure output.
- A workflow's declared `inputs`/`outputs` make it a typed function: every
  declared input must be bound **exactly once** at each `workflow` call site, a
  binding's kind must match the declaration (a `ref` is kind-preserving), and
  every reference must resolve in scope. `python -m engine lint` checks this
  offline (rules [L8–L10](./lint.md#rule-reference)).

Script step example:

```yaml
steps:
  - name: run-tests
    script:
      path: scripts/test.sh   # repo-relative, required
      args: ["--unit"]        # optional argv list
      timeout_minutes: 15     # optional, default 30
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
[ADR-0009](./adr/0009-workflows-as-typed-functions.md).

---

## WORKFLOW_STATE v1

The workflow engine persists state inside issue comments as a collapsed
JSON section between two markers:

```html
<!-- WORKFLOW_STATE_START -->
{ ...state JSON... }
<!-- WORKFLOW_STATE_END -->
```

Model: `engine/lib/workflow_state/workflow_state.py`
(`WorkflowState`, Pydantic `BaseModel`). Every agent step produces a fresh
full-snapshot state (no delta updates); the last state found in a comment body
is authoritative.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | int | Monotonically increasing invocation counter: the Nth state update in this workflow run (issue #821). Carried across the trigger->agent hand-off so a reset is detectable. |
| `schema_version` | int | Payload shape generation this state was written at (`STATE_SCHEMA_VERSION`, currently 4). Legacy blobs still carrying the old `version` key are read as generation 3, with that value migrated into `run_id` (issue #821). |
| `workflow` | string | Name of the active workflow. |
| `step` | string | Current step name. |
| `agent` | string | Agent role executing the current step. |
| `phase` | string | High-level phase (`analysis`, `planning`, `execution`, ...). |
| `history` | HistoryEntry[] | Summarized records of prior steps (see below). |
| `pinned_comment_ids` | int[] | Comment IDs injected **verbatim** into the next agent's context; every other included comment is injected as a summary/excerpt. |
| `current_context` | object | Free-form shared data between agents (pending tasks, constraints, decisions, `last_run_status`, ...). |
| `parent_workflow` | string \| null | Parent workflow name when inside a sub-workflow. Derived view of the second-to-last `position` frame. |
| `parent_workflow_step` | string \| null | Parent step that mounted the sub-workflow. |
| `position` | StepRef[] | The run's full nested call-stack — flat, outermost-first; the last frame is the step actually executing (issue #638). Engine-owned, carried forward across transitions: entry appends a frame, an intra-workflow move replaces the leaf, return-to-parent pops it (issue #812). |
| `issue` | IssueInfo \| null | Static issue metadata: `number`, `url`, `title`, `author`, `repo`. |
| `pull_request` | PullRequestInfo \| null | Same shape as `issue`, for PR contexts. |
| `dispatch` | Dispatch \| null | Outcome + runtime preferences of the current agent (see [Dispatch Object](#dispatch-object)). |
| `resolution` | StepResolution \| null | Full context of the step-resolution decision (debugging): resolved role, prompt location, matched rule, applied config, timestamp. |
| `gate_decision` | GateDecision \| null | Latest engine-owned human-gate decision (issue #648): `status` (`approved`/`rejected`), `decided_by`, `decided_at`, `feedback`, `comment_id`, and the `position` hold instance it consumes. |
| `gate_history` | GateDecision[] | Append-only audit of every gate decision (issue #648). |

### HistoryEntry

| Field | Type | Description |
|-------|------|-------------|
| `step` | string | Step name. |
| `agent` | string | Agent role. |
| `summary` | string | One-line summary of what happened. |
| `comment_ids` | int[] | GitHub comment IDs of the step output (multi-part agent messages are posted as several comments; legacy snapshots storing a scalar `comment_id` are coerced to a list on load). |
| `inject_context` | bool | Whether the comment should be included in the next agent's context. |
| `artifacts` | string[] | Paths to artifacts produced by the step. |
| `pin` | bool | Whether this entry's `comment_ids` are injected verbatim (their ids are unioned into `pinned_comment_ids` during state enrichment). |
| `result` | string \| null | Reported result (`approved`, `rejected`, ...). |
| `dispatch` | Dispatch \| null | Routing decision emitted by this step (same shape as the top-level [Dispatch Object](#dispatch-object)); `null` for legacy entries that predate the field. Makes the history a replayable movement log. |
| `position` | StepRef[] | The mount stack recorded for this step (issue #638). After every run `history[-1].position` equals `WorkflowState.position`; faithful stacks let the terminal unwind recover the ancestor chain one level at a time (issue #812). |
| `metrics` | StateMetrics \| null | Metrics of **this step's** agent invocation (full `Metrics` payload; issue #792). `history[-1].metrics` is the single metrics authority. |

### Annotated Example

```json
{
  "run_id": 1,
  "schema_version": 4,
  "workflow": "default",
  "step": "context",
  "agent": "context",
  "phase": "analysis",
  "history": [
    {"step": "analyze", "agent": "architect", "summary": "Issue analyzed, plan requested", "comment_ids": [1029384], "result": "advance", "dispatch": {"status": "advance", "task_hint": "context", "complexity": "high"}, "metrics": {"run_id": "1bb4941e-0bbf-432b-b79c-0b408feea07f", "model_name": "default", "agent_role": "architect", "input_tokens": 8365, "output_tokens": 1143, "max_context_window": 0, "tool_call_latency_ms": 19685.7, "agent_runtime_ms": 19685.7, "nb_tool_calls": 0, "tool_call_error_rate": 0.0, "review_fix_requests": 0}}
  ],
  "pinned_comment_ids": [],
  "current_context": {"last_run_status": "success"},
  "parent_workflow": null,
  "parent_workflow_step": null,
  "issue": {"number": 501, "url": "https://github.com/o/r/issues/501", "title": "write documentation", "author": "olivier-norture", "repo": "o/r"},
  "pull_request": null,
  "dispatch": {"status": "advance", "complexity": "high"},
  "resolution": {"role_name": "context", "matched_rule": "workflow:default/step:context", "timestamp": "2026-09-04T21:05:53Z"}
}
```

---

## External Reference JSON

Contract for the fenced ```json block `@@context` emits inside
`### EXTERNAL REFERENCES` of its context report. It is an **array** with one
object per reference; the human-readable entries and this block must cover the
same references. This block is the access path for tool-less agents (e.g. `@@architect`)
([features.md — External Reference Sharing](./features.md#external-reference-sharing)).

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | The reference URL. |
| `date` | string | ISO-8601 UTC fetch date. |
| `chunks` | string[] | Verbatim, unedited excerpts of the page relevant to the issue (≤ ~2,000 tokens per reference, ≈ ≤ 8,000 chars; ≤ ~20K tokens across the whole run). |
| `summary` | string | 1–3 sentences on the article's relevance/interest to the specific issue or question. |
| `score` | number | Float in `0.0`–`1.0`: level of interest/relevance to the issue (not source credibility). |

```json
[
  {
    "url": "https://docs.searxng.org/dev/search_api.html",
    "date": "2026-09-23T09:25:00Z",
    "chunks": ["SearXNG supports querying via a simple HTTP API ..."],
    "summary": "Documents the /search endpoint used by @@context; confirms results[].content is snippet-only.",
    "score": 0.9
  }
]
```

The block must be valid JSON: no comments, no trailing commas, no prose
outside the fence. There is no code model for this contract (prompt-only);
this table is the source of truth.

---

## Environment Variables Catalog

Variables the orchestrator sets/consumes per run:

| Variable | Set by | Description |
|----------|--------|-------------|
| `TARGET_NUMBER` | `event_normalizer.py` | Issue/PR number the run targets. |
| `TRIGGER_BODY` | `event_normalizer.py` | Raw text of the triggering issue body or comment. |
| `TRIGGER_AUTHOR` | `event_normalizer.py` | Author of the triggering event. |
| `EVENT_TYPE` | `event_normalizer.py` | GitHub event type (`issues`, `issue_comment`, `workflow_call`, ...). |
| `ROLE_NAME` | `resolve_config` / `resolve_workflow` | Resolved agent role (`architect`, `context`, `dev`, `review`, ...). |
| `PROMPT_LOCATION` | `resolve_config` / `resolve_workflow` | Path to the resolved prompt file. |
| `WORKFLOW_NAME` | `resolve_config` / `resolve_workflow` | Resolved workflow name. |
| `WORKFLOW_STEP` | `resolve_config` / `resolve_workflow` | Resolved current step name. |
| `WORKFLOW_NEXT` | `resolve_config` / `resolve_workflow` | Next step name after the current one. |
| `STEP_PROMPT_FILE` | `resolve_config` / `resolve_workflow` | Path to the step's standing instruction file (`steps[].prompt`). Empty when the step has no prompt. |
| `CONFIG_COMPLEXITY` | `resolve_config` | Effective tier for this run (`low`/`medium`/`high`): an explicit dispatch `complexity`, else the step's `steps[].complexity`, else `medium`. Drives `agents.<name>.complexity_overrides.<tier>` and the tier temperature floor (issue #787). |
| `RUN_SCRIPT` | `resolve_config` | `"true"` when the resolved step is a deterministic `script` step (no agent runs). |
| `SCRIPT_PATH` | `resolve_config` | Repo-relative path of the script to run (from `script.path`). |
| `SCRIPT_ARGS` | `resolve_config` | Space-joined argument string for the script (empty when none). |
| `SCRIPT_TIMEOUT_MINUTES` | `resolve_config` | Script timeout in minutes (empty → default 30). |
| `PARENT_WORKFLOW` | `resolve_config` / `resolve_workflow` | Parent workflow name when the run is nested in a sub-workflow mount; empty at top level. Derived from the second-to-last `position` frame. |
| `PARENT_WORKFLOW_STEP` | `resolve_config` | Step in the parent workflow that mounted a sub-workflow. |
| `WORKFLOW_POSITION` | `resolve_config` / `resolve_workflow` | Single-line JSON of the run's full mount stack (`WorkflowState.position`). |
| `WORKFLOW_POSITION_PATH` | `resolve_config` / `resolve_workflow` | Human-readable `wf/step\|wf/step\|...` rendering of `WORKFLOW_POSITION`. |
| `STEP_INPUTS` | `resolve_config` | Single-line JSON of a resolved step's typed-input wiring (issue #813): the consumer step's own bindings plus the call-frame chain (each frame's callee, call step and frozen bindings) derived from the carried-forward `WORKFLOW_POSITION`. Emitted only when the run actually wires something. Empty/absent otherwise. |
| `DEBUG` | user (`DEBUG=true` in body) / flag | Enables verbose debug logging. |
| `OPENROUTER_API_KEY` | repo secret | LLM provider token (OpenRouter). |
| `LOCAL_VLLM_API_TOKEN` | repo secret (optional) | Local vLLM provider token. |
| `COMMANDCODE_API_KEY` | repo secret (optional) | CommandCode subscription token — see [subscription-plans.md](./subscription-plans.md). |
| `GH_PAT` | repo secret | GitHub PAT used for git push and API calls. |

`event_normalizer.py` emits `KEY=value` pairs (multi-line values use HEREDOC
form `KEY<<EOF ... EOF`) for `eval $(...)` consumption; `query_context.py`
writes to `GITHUB_ENV` the same way.

---

## Bot Marker Conventions

| Marker | Purpose |
|--------|---------|
| `<!-- bot-workflow-terminal -->` | Universal bot marker prepended (line 1) to every CI-posted comment. Makes human-vs-bot classification deterministic. Defined as `BOT_MARKER` in `github_client.py`. |
| `<!-- bot-agent-{name} -->` | Per-agent marker identifying which agent posted a comment (e.g., `<!-- bot-agent-architect -->`). Used by circuit breaker and metrics extraction. |
| `<!-- bot-agent-script -->` | Pseudo-role marker for deterministic `script` steps (issue #541): the step output is posted under this fixed tag so repeated runs update rather than duplicate, and circuit-breaker marker counting stays intact. |
| `<!-- AGENT_RUN_CONFIG ... -->` | Dispatch sentinel — see [AGENT_RUN_CONFIG](#agent_run_config). Canonical regex in `validate_output.py`: `<!--(?:[ \t])*AGENT_RUN_CONFIG[ \t]+(\{.*?\})(?:[ \t])*-->` (single-line, no DOTALL). |
| `<!-- WORKFLOW_STATE_START -->` ... `<!-- WORKFLOW_STATE_END -->` | Collapsed JSON workflow-state section — see [WORKFLOW_STATE v1](#workflow_state-v1). Per-step agent metrics live in its `history` entries. |
| `<!-- po-agent-state: round=N,status=S -->` | Issue-refinement agent state markers (see `AGENTS.md`). |
