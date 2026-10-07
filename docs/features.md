# Features

Per-feature deep-dives. Table of contents:

1. [Human Commands](#human-commands)
2. [Task Decomposition](#task-decomposition)
3. [Safety Mechanisms](#safety-mechanisms)
4. [Metrics & Summarization](#metrics--summarization)
5. [Execution Layer](#execution-layer)
6. [Remote Workflows (Zero-Copy)](#remote-workflows-zero-copy)

---

## Human Commands

A human steers the workflow by writing commands in an issue body or comment.
Commands are parsed by the `trigger_commands` sub-domain
(`engine/lib/resolve_config/sub_domains/trigger_commands/trigger_commands.py`)
into a frozen `TriggerCommands` dataclass: `is_bot`, `mention_agent`,
`workflow_cmd`, `force_next`, `resume`, `debug`.

| Command | Effect |
|---------|--------|
| `@@<agent>` | Run the named agent directly (`genius`, `ctx`, `dev`, `review`, ...). If the agent matches a workflow's `trigger.agents`, that workflow's first step runs. |
| `/nebuko run workflow --name <name> [--step <step>]` | Start (or switch to) the named workflow, regardless of current state. `--step` seeds a **fresh** run directly at `<step>` instead of the first step (issue #789) — useful to enter `plan-orchestration` at `plan`/`scaffold` without replaying `architect`. An unknown or unsafe step name fails loudly. Human-only. |
| `/nebuko resume` | Resume the held workflow step with your input (bare), or advance if there is no active dispatch. |
| `/nebuko force-step step --name <step>` | Jump directly to `<step>` of the **active** workflow, regardless of state. Unlike `run --step`, this replays/forks within the current run (it requires an active workflow). |
| `/nebuko delete comments [--issue <id>] [--from <comment-id>]` | Human-only batch deletion of issue comments. `--issue <id>` selects the target issue (mandatory on the CLI surface; auto-filled with the invoking issue when the command is posted in that issue). Optional `--from <comment-id>` deletes that comment **and every later one**; omit it to delete **all** comments. Terminal command: it runs the deterministic deletion step and no agent. Requires `GH_PAT` with `issues:write`. |
| `/nebuko clean comments` | Alias of `/nebuko delete comments` (issue #679). Terminal; runs the deterministic deletion step and no agent. |
| `DEBUG=true` | Routes to the debug/recovery path instead of normal routing. |

**Precedence:** human commands always override state-based routing (see the
cascade in [architecture.md — Routing Cascade](./architecture.md#routing-cascade)):
`resume` > `force-step` > `delete comments` > `run workflow` >
`@@mention` > dispatch-based routing > next-step > bot fallback. This guarantee
exists so explicit human intent is never absorbed by an active workflow's
`dispatch` object. `delete comments` is a **terminal** human command: it runs
the deterministic deletion step and never invokes an agent.

Note: **bot agents cannot use `@@mention`** (only humans may); bots hand off via
the `AGENT_RUN_CONFIG` sentinel ([reference.md](./reference.md#agent_run_config)).
The loop guard additionally skips a run when a human is pinged *alongside* an
agent, so the human gets to orchestrate.

---

## Task Decomposition

When `@@genius` judges a task too large for one implementation round, it emits
a **split plan** as an XML-tagged JSON block inside its final message:

```
<splitPlan>
{
  "tasks": [
    {"title": "Add retry helper", "body": "...", "labels": ["enhancement"], "dependencies": []},
    {"title": "Wire retry into breaker", "body": "...", "labels": [], "dependencies": [1]}
  ],
  "auto_trigger": true
}
</splitPlan>
```

Pipeline:

1. **Extraction** — `extract_split_plan()` (sub-domain `split_plan`) pulls the
   block out of the agent's final message; `strip_split_plan()` removes it from
   the posted comment so humans see clean output.
2. **Validation & creation** — the GHA *Process Split Plan* step runs
   `issue_creation.py create --parent N --plan plan.json`, which validates the
   schema (non-empty `tasks[]`, list-type `dependencies[]`) and creates one
   sub-issue per task under the parent via the GitHub REST API
   (`GitHubClient` adapter, Port/Adapter pattern).
3. **Dependency tracking** — each sub-issue records its dependency list;
   `list` and `link` subcommands inspect/link the tree.
4. **Auto-trigger** — with `auto_trigger: true`, ready sub-issues are triggered
   automatically once their dependencies complete.
5. **Completion notification** — the `subtask-completion-notifier` GHA job
   watches sub-issues; when all tasks of a split plan are done, it posts a
   summary on the parent issue.

`@@genius` resolves the dependency order and triggers ready tasks; each
sub-issue then runs its own full workflow chain independently.

---

## Safety Mechanisms

Three independent guards prevent runaway automation. All live in
`engine/lib/` and run *before* an agent executes.

### Circuit Breaker — `circuit_breaker.py`

Counts `<!-- bot-agent-{name} -->` markers across recent issue comments (the
universal bot marker itself is excluded). When the count reaches the threshold
(input `circuit_breaker_threshold`, default **15**), the run is skipped and a
warning comment is posted asking a human to review the issue. Output:
`CIRCUIT_BREAKER_TRIPPED=true/false`.

### Loop Guard — `loop_guard.py`

Two checks:

1. **Same-agent loop** — if the triggering bot comment was posted by the very
   agent about to be invoked, skip. Cross-agent calls remain allowed
   (`genius → ctx` is fine).
2. **Human + agent ping** — if a human is pinged alongside an agent, skip, so
   the human (not the machine) orchestrates the next move.

Output: `SKIP_AGENT=true/false`.

### Summarizer Gate — `summarizer_gate.py`

Counting-only (it never blocks a run): when non-bot-user comments reach the
threshold (input `summarizer_threshold`, default **100**) *and* no
`<!-- bot-agent-summary -->` comment exists yet, the summarizer agent is
triggered to produce a structured summary, keeping long issues navigable.
Output: `SUMMARIZER_TRIGGERED=true/false`.

GitHub REST rate limits that constrain these comment-fetching guards are
documented in [github-api-limits.md](./github-api-limits.md).

---

## Metrics & Summarization

Every agent run self-reports metrics in an HTML comment appended to its output:

```
<!-- agent-metrics: {"run_id": "...", "model_name": "...", "agent_role": "dev",
 "input_tokens": 492897, "output_tokens": 12638, "tool_call_latency_ms": 12562,
 "agent_runtime_ms": 200998, "nb_tool_calls": 29, "tool_call_error_rate": 0.0, ...} -->
```

- **Schema** — the canonical `Metrics` dataclass lives in
  `engine/lib/metrics_schema.py` (single source of truth; both writers and
  readers import from it). The compact subset embedded in workflow state is
  `StateMetrics` (see [reference.md — WORKFLOW_STATE v1](./reference.md#workflow_state-v1)).
- **Collection** — the `collect_metrics` feature of `agent_script` extracts
  token counts, latencies, tool-call statistics, and error rates from the
  OpenHands conversation after each run.
- **Issue-close aggregation** — `summarize_metrics.py` (GHA job
  `summarize-metrics`, triggered on issue close) fetches all comments, extracts
  every `agent-metrics` block, aggregates by model name (tokens, runtime, error
  rate), and posts a summary table.
- **Qualitative summary** — separately, the `summarize` agent produces a
  structured JSON narrative of the issue (decisions, files, pending actions),
  gated by the summarizer gate above.

---

## Execution Layer

The agent execution layer is the `agent_script` package
(`engine/lib/agent_script/`). Entry point `agent_script.py` orchestrates a
chain of features: `load_prompt` → `resolve_llm_config` → `run_agent` →
`build_output` (with `collect_metrics`, `ensure_pr`, `extract_split_plan`).

### Supervised execution — `run_agent`

The conversation runs via the async `arun()` API of the OpenHands SDK so that
a **watchdog** can call `conversation.interrupt()` to cancel the in-flight
task mid-LLM-call (issue #489). Timeouts are budget-based:

| Knob | Env var | Value in this repo |
|------|---------|---------|
| GHA hard stop | step `timeout-minutes` | 30 min |
| Main run budget | `AGENT_SOFT_TIMEOUT_MINUTES` | 20 min |
| Wind-down budget | `WIND_DOWN_TIMEOUT_MINUTES` | 10 min |

When the soft budget elapses the agent is **not** hard-killed by the GitHub
Actions timeout: it receives a **wind-down prompt** asking it to conclude its
work (post its answer, dispatch, and checkpoint any code) within the wind-down
budget. A gracefully timed-out run feeds the recovery-log pipeline, so the
next run resumes from a fresh session (see ADR-0002, issue #639).

### Deterministic PR fallback — `ensure_pr`

Guarantees the "checkpoint the work in a PR" requirement even if the wind-down
run is uncooperative — a pure git/gh subprocess sequence, no LLM involved:

1. Commit uncommitted working-tree changes (if any).
2. Push the current branch to `origin`.
3. Open a PR if none exists for the branch (idempotent).

### Recovery trace extraction

`extract_recovery_trace` captures the tail of an interrupted run so a resumed
or debugged run knows exactly where the previous one stopped.

### Long-output splitting — `message_splitter`

GitHub comment bodies have a size limit; `split_message` splits long final
messages at paragraph → line → sentence boundaries (hard split as last resort),
keeping each part under 10,000 characters. Extra parts are posted as additional
comment parts by the GHA *Post Additional Message Parts* step.

---

## External Reference Sharing

Contract for the web references `@@ctx` collects, so downstream agents
actually see page content rather than 1–2 sentence SearXNG snippets:

- **Inline curated JSON (agent access path).** `@@ctx` reads each reference
  page in full, then emits a fenced ```json block inside the
  `### EXTERNAL REFERENCES` section of its report: one object per reference
  with `url`, `date` (ISO-8601 UTC fetch date), `chunks` (verbatim, unedited
  excerpts, ≤ ~2K tokens per article → ≤ ~20K across a run), `summary`
  (1–3 sentences on relevance to the issue), and `score` (0.0–1.0 relevance,
  not credibility). Schema:
  [reference.md — External Reference JSON](./reference.md#external-reference-json).
  This rides the normal comment-history injection, so tool-less agents
  (notably `@@genius`) consume the content with zero new machinery.

Fetch failures degrade gracefully: the reference stays in the report with the
search snippet as its sole chunk, the failure is noted in its summary, and it
is scored accordingly.

---

## Remote Workflows (Zero-Copy)

Consumer repositories do not need to vendor the workflow YAML files from this
repo. `remote_workflow.py`
(`engine/lib/remote_workflow.py`) provides zero-copy fetching:

```python
from remote_workflow import fetch_remote_workflow
path = fetch_remote_workflow(
    repo="https://github.com/org/shared-workflows.git",
    ref="v1.2.3",
    workflow_name="deploy.yaml",
)
```

- **Mechanism** — shallow git clone/fetch of the pinned commit or tag, then
  locate the requested workflow YAML inside it.
- **Caching** — previously fetched repos are cached under
  `~/.gh-workflows/cache/`; re-fetches of the same ref are no-ops.
- **Precedence** — a local `workflows/<name>.yaml` always wins over a remotely
  fetched workflow of the same name, so consumers can fork any built-in
  workflow by dropping a local copy.
- **Update flow** — consumers pick up workflow fixes/features by bumping the
  ref, without any file changes in their own repository.

The consumer-side entry point is [`workflow.yaml`](../workflow.yaml), a thin
`workflow_call` wrapper that invokes this repo's reusable orchestrator with the
consumer's `agent_config_path` and secrets. For the full orchestrator step
reference, see [default-workflow.md](./default-workflow.md).
