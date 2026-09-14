# Reference

Machine-readable contracts used by git-flow-ai: the `AGENT_RUN_CONFIG` dispatch
sentinel, the workflow YAML grammar, the `WORKFLOW_STATE` v1 JSON schema, the
environment-variable catalog, and the bot marker conventions.

---

## AGENT_RUN_CONFIG

Agents communicate hand-offs using a single-line HTML-comment sentinel that
carries a compact JSON payload. The canonical matching pattern is defined in
`.github/scripts/validate_output.py`; all consumers (output building, comment
preparation) reuse it from there.

```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "medium"} -->
```

Single line + HTML comment means no multi-line boundary ambiguity and no leak
into rendered GitHub comments. A sentinel split across lines never matches.

### Dispatch Object

The current dispatch contract (see
`.github/scripts/workflow_state/sub_domains/dispatch/dispatch.py`):

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Agent outcome: `advance`, `loop`, `hold`, or `terminate`. The workflow engine resolves routing from this plus the workflow definition. |
| `task_hint` | string | Optional suggested next agent (overrides workflow YAML). |
| `complexity` | string | Complexity for the downstream agent: `low`, `medium`, `high`. Auto-adjusts temperature (see [configuration.md](./configuration.md)). |
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
| `reason` | string | Optional free-text explanation of the dispatch decision. |

Defaults: `status: "advance"`, `complexity: "medium"`, `reasoning: "none"`,
everything else empty/null.

### Legacy Flat Fields (Backward Compatibility)

Older sentinels use flat fields instead of `dispatch` (see the README schema
table): `task` (required — the target agent role), `skills` (string[]),
`complexity`, `reasoning`, `round` (dev↔review callback counter),
`temperature`, `top_p`, `top_k`, `repetition_penalty`, `model`, `workflow`,
`step`, `branch`.

Examples:

```
<!-- AGENT_RUN_CONFIG {"task": "review", "complexity": "medium"} -->
<!-- AGENT_RUN_CONFIG {"task": "dev", "round": 2} -->
<!-- AGENT_RUN_CONFIG {"task": "ctx", "workflow": "default", "step": "context"} -->
<!-- AGENT_RUN_CONFIG {"task": "dev", "complexity": "low", "top_p": 0.1, "top_k": 10, "repetition_penalty": 1.2} -->
```

**Rule:** human users trigger agents with `@@mention`; only agents may use
`AGENT_RUN_CONFIG`. Bot agents cannot use `@@mention` to avoid self-triggering
loops.

---

## Workflow YAML Grammar

Workflows are first-class YAML definitions in `workflows/` (local) or fetched
from the git-flow-ai repo (zero-copy). See [workflows.md](./workflows.md).

| Key | Scope | Type | Required | Description |
|-----|-------|------|----------|-------------|
| `name` | file | string | yes | Workflow name (must match the filename). |
| `description` | file | string | no | Human-readable description. |
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
| `steps[].skill` | step | string[] | no | Skills attached to the step prompt (files in `skills/`). |
| `steps[].prompt` | step | string | no | Standing instruction injected into this step's agent context (multi-line YAML blocks supported). See [workflows.md](./workflows.md#step-instructions-prompt). |
| `steps[].human_gate` | step | bool | no | Security boundary: the engine intercepts the agent's `advance` and holds the workflow until a human releases it with `/workflow resume` (see [workflows.md](./workflows.md#human-gates)). |

Minimal valid workflow:

```yaml
name: my-workflow
description: "A minimal two-step workflow"
trigger:
  agents: [genius]
steps:
  - name: analyze
    agent: genius
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

---

## WORKFLOW_STATE v1

The workflow engine persists state inside issue comments as a collapsed
JSON section between two markers:

```html
<!-- WORKFLOW_STATE_START -->
{ ...state JSON... }
<!-- WORKFLOW_STATE_END -->
```

Model: `.github/scripts/workflow_state/workflow_state.py`
(`WorkflowState`, Pydantic `BaseModel`). Every agent step produces a fresh
full-snapshot state (no delta updates); the last state found in a comment body
is authoritative.

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | Monotonically increasing version number (1 in v1). |
| `workflow` | string | Name of the active workflow. |
| `step` | string | Current step name. |
| `agent` | string | Agent role executing the current step. |
| `phase` | string | High-level phase (`analysis`, `planning`, `execution`, ...). |
| `history` | HistoryEntry[] | Summarized records of prior steps (see below). |
| `pinned_comment_ids` | int[] | Comment IDs whose full content should be re-fetched at runtime for high-fidelity context. |
| `metrics` | StateMetrics \| null | Metrics of the most recent agent invocation (subset of the full `Metrics` schema). |
| `current_context` | object | Free-form shared data between agents (pending tasks, constraints, decisions, `last_run_status`, ...). |
| `parent_workflow` | string \| null | Parent workflow name when inside a sub-workflow. |
| `parent_workflow_step` | string \| null | Parent step that mounted the sub-workflow. |
| `issue` | IssueInfo \| null | Static issue metadata: `number`, `url`, `title`, `author`, `repo`. |
| `pull_request` | PullRequestInfo \| null | Same shape as `issue`, for PR contexts. |
| `dispatch` | Dispatch \| null | Outcome + runtime preferences of the current agent (see [Dispatch Object](#dispatch-object)). |
| `resolution` | StepResolution \| null | Full context of the step-resolution decision (debugging): resolved role, prompt location, matched rule, applied config, timestamp. |

### HistoryEntry

| Field | Type | Description |
|-------|------|-------------|
| `step` | string | Step name. |
| `agent` | string | Agent role. |
| `summary` | string | One-line summary of what happened. |
| `comment_ids` | int[] | GitHub comment IDs of the step output (multi-part agent messages are posted as several comments; legacy snapshots storing a scalar `comment_id` are coerced to a list on load). |
| `inject_context` | bool | Whether the comment should be included in the next agent's context. |
| `artifacts` | string[] | Paths to artifacts produced by the step. |
| `pin` | bool | Whether the comment is pinned for re-fetch. |
| `result` | string \| null | Reported result (`approved`, `rejected`, ...). |

### Annotated Example

```json
{
  "version": 1,
  "workflow": "default",
  "step": "context",
  "agent": "ctx",
  "phase": "analysis",
  "history": [
    {"step": "analyze", "agent": "genius", "summary": "Issue analyzed, plan requested", "comment_ids": [1029384], "result": "advance"}
  ],
  "pinned_comment_ids": [],
  "metrics": {
    "run_id": "1bb4941e-0bbf-432b-b79c-0b408feea07f",
    "model_name": "default",
    "agent_role": "genius",
    "input_tokens": 8365,
    "output_tokens": 1143,
    "max_context_window": 0,
    "tool_call_latency_ms": 19685.7,
    "agent_runtime_ms": 19685.7,
    "nb_tool_calls": 0,
    "tool_call_error_rate": 0.0,
    "review_fix_requests": 0
  },
  "current_context": {"last_run_status": "success"},
  "parent_workflow": null,
  "parent_workflow_step": null,
  "issue": {"number": 501, "url": "https://github.com/o/r/issues/501", "title": "write documentation", "author": "olivier-norture", "repo": "o/r"},
  "pull_request": null,
  "dispatch": {"status": "advance", "complexity": "high"},
  "resolution": {"role_name": "ctx", "matched_rule": "workflow:default/step:context", "timestamp": "2026-09-04T21:05:53Z"}
}
```

---

## Environment Variables Catalog

Variables the orchestrator sets/consumes per run:

| Variable | Set by | Description |
|----------|--------|-------------|
| `TARGET_NUMBER` | `event_normalizer.py` | Issue/PR number the run targets. |
| `TRIGGER_BODY` | `event_normalizer.py` | Raw text of the triggering issue body or comment. |
| `TRIGGER_AUTHOR` | `event_normalizer.py` | Author of the triggering event. |
| `EVENT_TYPE` | `event_normalizer.py` | GitHub event type (`issues`, `issue_comment`, `workflow_call`, ...). |
| `ROLE_NAME` | `resolve_config` / `resolve_workflow` | Resolved agent role (`genius`, `ctx`, `dev`, `review`, ...). |
| `PROMPT_LOCATION` | `resolve_config` / `resolve_workflow` | Path to the resolved prompt file. |
| `WORKFLOW_NAME` | `resolve_config` / `resolve_workflow` | Resolved workflow name. |
| `WORKFLOW_STEP` | `resolve_config` / `resolve_workflow` | Resolved current step name. |
| `WORKFLOW_NEXT` | `resolve_config` / `resolve_workflow` | Next step name after the current one. |
| `STEP_PROMPT_FILE` | `resolve_config` / `resolve_workflow` | Path to the step's standing instruction file (`steps[].prompt`). Empty when the step has no prompt. |
| `RUN_SCRIPT` | `resolve_config` | `"true"` when the resolved step is a deterministic `script` step (no agent runs). |
| `SCRIPT_PATH` | `resolve_config` | Repo-relative path of the script to run (from `script.path`). |
| `SCRIPT_ARGS` | `resolve_config` | Space-joined argument string for the script (empty when none). |
| `SCRIPT_TIMEOUT_MINUTES` | `resolve_config` | Script timeout in minutes (empty → default 30). |
| `PARENT_WORKFLOW_STEP` | `resolve_config` | Step in the parent workflow that mounted a sub-workflow. |
| `DEBUG` | user (`DEBUG=true` in body) / flag | Enables debug-mode routing (recover) and verbose logs. |
| `OPENROUTER_API_KEY` | repo secret | LLM provider token (OpenRouter). |
| `LOCAL_VLLM_API_TOKEN` | repo secret (optional) | Local vLLM provider token. |
| `GH_PAT` | repo secret | GitHub PAT used for git push and API calls. |

`event_normalizer.py` emits `KEY=value` pairs (multi-line values use HEREDOC
form `KEY<<EOF ... EOF`) for `eval $(...)` consumption; `query_context.py`
writes to `GITHUB_ENV` the same way.

---

## Bot Marker Conventions

| Marker | Purpose |
|--------|---------|
| `<!-- bot-workflow-terminal -->` | Universal bot marker prepended (line 1) to every CI-posted comment. Makes human-vs-bot classification deterministic. Defined as `BOT_MARKER` in `github_client.py`. |
| `<!-- bot-agent-{name} -->` | Per-agent marker identifying which agent posted a comment (e.g., `<!-- bot-agent-genius -->`). Used by circuit breaker and metrics extraction. |
| `<!-- bot-agent-script -->` | Pseudo-role marker for deterministic `script` steps (issue #541): the step output is posted under this fixed tag so repeated runs update rather than duplicate, and circuit-breaker marker counting stays intact. |
| `<!-- bot-agent-summary -->` | Marks a summarizer output; the summarizer gate checks for it before triggering again. |
| `<!-- AGENT_RUN_CONFIG ... -->` | Dispatch sentinel — see [AGENT_RUN_CONFIG](#agent_run_config). Canonical regex in `validate_output.py`: `<!--(?:[ \t])*AGENT_RUN_CONFIG[ \t]+(\{.*?\})(?:[ \t])*-->` (single-line, no DOTALL). |
| `<!-- WORKFLOW_STATE_START -->` ... `<!-- WORKFLOW_STATE_END -->` | Collapsed JSON workflow-state section — see [WORKFLOW_STATE v1](#workflow_state-v1). |
| `<!-- agent-metrics: {...} -->` | Per-run metrics (full `Metrics` schema JSON), extracted and aggregated by `summarize_metrics.py` on issue close. May span multiple lines. |
| `<!-- issue-agent-state: round=N,status=S -->` | Issue-refinement agent state markers (see `AGENTS.md`). |
