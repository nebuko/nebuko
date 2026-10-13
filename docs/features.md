# Features

Per-feature deep-dives. Table of contents:

1. [Human Commands](#human-commands)
2. [Safety Mechanisms](#safety-mechanisms)
3. [Metrics](#metrics)
4. [Execution Layer](#execution-layer)
5. [Remote Workflows (Zero-Copy)](#remote-workflows-zero-copy)

---

## Human Commands

A human steers the workflow by writing commands in an issue body or comment.
Commands are parsed by the `trigger_commands` sub-domain
(`engine/lib/resolve_config/sub_domains/trigger_commands/trigger_commands.py`)
into a frozen `TriggerCommands` dataclass: `is_bot`, `mention_agent`,
`workflow_cmd`, `force_next`, `resume`, `debug`.

| Command | Effect |
|---------|--------|
| `@@<agent>` | Run the named agent directly (`architect`, `context`, `dev`, `review`, ...). To start a workflow, use `/nebuko run workflow --name <name>` — which enters at the workflow's first step. |
| `/nebuko run workflow --name <name> [--step <step>[.<step>...]]` | Start (or switch to) the named workflow, regardless of current state. `--step` seeds a **fresh** run directly at `<step>` instead of the first step (issue #789) — useful to enter `plan-orchestration` at `plan`/`scaffold` without replaying `architect`. A **dotted** selector (`sub_impl.step_3`, `sub_impl.step_3.review`) descends into mounted sub-workflows to address a non-first nested step (issue #822). An unknown, malformed, or not-yet-scaffolded target fails loudly. Human-only. |
| `/nebuko resume` | Resume the held workflow step with your input (bare), or advance if there is no active dispatch. On a `human_gate:*` hold it is **inert** (points at the gate commands) — use `/nebuko approve gate` / `/nebuko reject gate` there. |
| `/nebuko approve gate [--feedback "..."]` | Release a `human_gate` hold: record an engine-owned **`GateDecision`** (`approved`) and route via the gated step's `on_result: approved` successor. Handled inline by the engine, never routed to an agent. Authorized humans only (`OWNER`/`MEMBER`/`COLLABORATOR`), always-on regardless of `CALLER_AUTH` (issue #648). |
| `/nebuko reject gate [--feedback "..."]` | Release a `human_gate` hold as **rejected**: with `--feedback` the gated step is **re-run** with the feedback injected (gate re-arms on completion); without feedback it routes via `on_result: rejected`. Same identity rules as `approve gate`. |
| `/nebuko force-step step --name <step>[.<step>...]` | Jump directly to `<step>` of the **active** workflow, regardless of state. A dotted selector descends mounted sub-workflows (issue #822), exactly as for `run --step`. Unlike `run --step`, this replays/forks within the current run (it requires an active workflow). |
| `/nebuko delete comments [--issue <id>] [--from <comment-id>]` | Human-only batch deletion of issue comments. `--issue <id>` selects the target issue (mandatory on the CLI surface; auto-filled with the invoking issue when the command is posted in that issue). Optional `--from <comment-id>` deletes that comment **and every later one**; omit it to delete **all** comments. Terminal command: it runs the deterministic deletion step and no agent. Requires `GH_PAT` with `issues:write`. |
| `/nebuko clean comments [--issue <id>] [--from <comment-id>]` | Alias of `/nebuko delete comments` (issue #679), accepting the same optional `--issue`/`--from` flags. Terminal; runs the deterministic deletion step and no agent. |
| `/nebuko update circuit-breaker --value <n>` | Human-only, per-issue circuit-breaker override (issue #839). Persists `limit = <n>` in the issue's `workflow_state`; the guard then uses `state override → CIRCUIT_BREAKER_THRESHOLD env → 25`. Raising the value above the current agent-call count **un-trips** the issue; lowering it tightens a runaway issue. Terminal, agent-free: it consumes no call budget. Bots are ignored. |
| `DEBUG=true` | Enables verbose debug logging for the run. |

**Multiline `--feedback`:** the command line is the grammar line, but a terminal
`--feedback` flag spans the remainder of the comment body verbatim, newlines
preserved (issue #919). So a bare `--feedback` with the payload on following
lines is valid:

```
/nebuko approve gate --feedback
1. Flat
2. Remove this section
3. cache_hit_token is fine
```

A `--feedback` with nothing after it (only blank lines) is still rejected as a
bare flag value.

**Precedence:** human commands always override state-based routing (see the
cascade in [architecture.md — Routing Cascade](./architecture.md#routing-cascade)):
`approve/reject gate` > `resume` > `force-step` > `delete comments` >
`update circuit-breaker` > `run workflow` > `@@mention` > dispatch-based
routing > next-step > bot fallback. This guarantee exists so explicit human
intent is never absorbed by an active workflow's `dispatch` object.
`delete comments` and `update circuit-breaker` are **terminal** human commands:
they run a deterministic step and never invoke an
agent.

Note: **bot agents cannot use `@@mention`** (only humans may); bots hand off via
the `AGENT_RUN_CONFIG` sentinel ([reference.md](./reference.md#agent_run_config)).
The loop guard additionally skips a run when a human is pinged *alongside* an
agent, so the human gets to orchestrate.

An invalid invocation — an unknown flag (e.g. `/nebuko run workflow --default`),
missing resource, or a leading `/` that matches no command — replies on the
thread with a usage hint instead of failing silently in the CI log. The reply
costs no LLM call and never re-triggers the engine (decision recorded in
[ADR-0004](./adr/0004-propagate-human-command-errors-to-thread.md), issue #804).

A malformed `/nebuko` invocation is a **command attempt**: the grammar alone
decides it, so it is answered with its usage hint *before* the routing cascade
and can never be absorbed by an active workflow `dispatch` (issue #867,
[ADR-0011](./adr/0011-command-attempts-resolved-by-grammar.md)). This is what
makes a mistyped command such as `/nebuko clean comments --from <id>` safe: it
either executes (with `--from`, the scoped deletion) or gets an answer — it
never boots an agent. The `--from <comment-id>` scope deletes that comment and
every later one on the target issue; omit it to delete all comments.

---

## Safety Mechanisms

Three independent guards prevent runaway automation. All live in
`engine/lib/` and run *before* an agent executes.

### Circuit Breaker — `circuit_breaker.py`

Counts `<!-- bot-agent-{name} -->` markers across recent issue comments (the
universal bot marker itself is excluded). When the count reaches the threshold
(input `circuit_breaker_threshold`, operative default **25**), the run is
skipped and a warning comment is posted asking a human to review the issue.
Output: `CIRCUIT_BREAKER_TRIPPED=true/false`. A human can raise (or lower) the
budget for a single issue with `/nebuko update circuit-breaker --value <n>`
(issue #839); the override wins over the workflow parameter.

### Loop Guard — `loop_guard.py`

Two checks:

1. **Same-agent loop** — if the triggering bot comment was posted by the very
   agent about to be invoked, skip. Cross-agent calls remain allowed
   (`architect → context` is fine).
2. **Human + agent ping** — if a human is pinged alongside an agent, skip, so
   the human (not the machine) orchestrates the next move.

Output: `SKIP_AGENT=true/false`.

GitHub REST rate limits that constrain these comment-fetching guards are
documented in [github-api-limits.md](./github-api-limits.md).

---

## Metrics

Every agent step self-reports metrics, embedded as the `metrics` field of its
entry in the `workflow_state` `history` (issue #792 — there is no longer a
separate hidden HTML comment):

```json
"history": [
  {"step": "implement", "agent": "dev", "summary": "...",
   "metrics": {"run_id": "...", "model_name": "...", "agent_role": "dev",
     "input_tokens": 492897, "output_tokens": 12638, ...}}
]
```

- **Schema** — the canonical `Metrics` dataclass lives in
  `engine/lib/metrics_schema.py` (single source of truth; both writers and
  readers import from it). The per-step record embedded in workflow state is
  `StateMetrics` (see [reference.md — WORKFLOW_STATE v1](./reference.md#workflow_state-v1)).
- **Collection** — the `collect_metrics` feature of `agent_script` extracts
  token counts, latencies, tool-call statistics, and error rates from the
  OpenHands conversation after each run, and `build_state` attaches them to the
  step's `HistoryEntry`.
- **Cost estimation (issue #714)** — cost resolves through a precedence chain:
  a provider/SDK-reported cost (cache-aware) always wins; otherwise the token
  counts are priced locally with **four rates** (input, output, cache-read,
  cache-write, in USD per 1M tokens). Where agent-config sets only some fields,
  the missing tiers are completed per field from a **default price service** —
  an OpenRouter-backed table fetched once and file-cached under
  `~/.nebuko/cache/prices/` (env `NEBUKO_PRICE_CACHE_DIR`), fully fail-open.
  This is what lets subscription providers (CommandCode, OpenCode), which report
  no per-token cost and ship no prices, produce non-zero estimates — CommandCode
  by exact org-qualified id, OpenCode Go through an unambiguous-basename
  fallback for its org-less paths. `0` remains the documented outcome only when
  neither config nor the service knows the model. Because the service is
  org-aware, a model priced only by the operator's config still resolves too.
- **Issue-close aggregation** — `agent_metrics.py` (GHA job
  `agent-metrics`, triggered on issue close) fetches all comments, reads the
  per-step metrics from each comment's `workflow_state` snapshot (de-duplicating
  entries repeated across accumulating snapshots), aggregates by model name
  (tokens, runtime, error rate), and posts a summary table.

---

## Execution Layer

The agent execution layer is the `agent_script` package
(`engine/lib/agent_script/`). Entry point `agent_script.py` orchestrates a
chain of features: `load_prompt` → `resolve_llm_config` → `run_agent` →
`build_output` (with `collect_metrics`, `ensure_pr`).

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

### Team context injection — `team_context`

`resolve_config/features/team_context/team_context.py` assembles the two shared
sections injected into **every** agent's system prompt (issue #476):

- the **generic exit rules** (mandatory feedback + exactly-one-sentinel), moved
  out of the individual prompt files so each built prompt contains one copy;
- a generated **"Your Team"** block — the agent's own role and its teammates'
  roles from the live config roster, the delegation rule (`call_agent`, not
  `@@`), the call-back reminder, and a caller line when the run was bot-invoked.

It is pure text generation over the parsed `AppConfig`; an unreadable roster or
an unknown agent degrades to no team section rather than failing the run (same
contract as `config.load_agent_names`). See
[agents-and-skills.md — Team Context Injection](./agents-and-skills.md#team-context-injection).

### Long-output splitting — `message_splitter`

GitHub comment bodies have a size limit; `split_message` splits long final
messages at paragraph → line → sentence boundaries (hard split as last resort),
keeping each part under 10,000 characters. Extra parts are posted as additional
comment parts by the GHA *Post Additional Message Parts* step.

---

## External Reference Sharing

Contract for the web references `@@context` collects, so downstream agents
actually see page content rather than 1–2 sentence search snippets:

- **Inline curated JSON (agent access path).** `@@context` reads each reference
  page in full, then emits a fenced ```json block inside the
  `### EXTERNAL REFERENCES` section of its report: one object per reference
  with `url`, `date` (ISO-8601 UTC fetch date), `chunks` (verbatim, unedited
  excerpts, ≤ ~2K tokens per article → ≤ ~20K across a run), `summary`
  (1–3 sentences on relevance to the issue), and `score` (0.0–1.0 relevance,
  not credibility). Schema:
  [reference.md — External Reference JSON](./reference.md#external-reference-json).
  This rides the normal comment-history injection, so tool-less agents
  (notably `@@architect`) consume the content with zero new machinery.

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

The consumer-side entry point is `workflow.yaml` — the thin `workflow_call`
wrapper each consumer repo writes to invoke this repo's reusable orchestrator
with their `agent_config_path` and secrets (a ready-to-paste example lives in
[adoption.md](./adoption.md)). For the full orchestrator step reference, see
[default-workflow.md](./default-workflow.md).
