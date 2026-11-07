---
title: "WORKFLOW_STATE v1 schema"
audience: contributor
sidebar_group: "Reference"
sidebar_label: "WORKFLOW_STATE schema"
sidebar_order: 13
---

# WORKFLOW_STATE v1 schema

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
| `schema_version` | int | Payload shape generation this state was written at (`STATE_SCHEMA_VERSION`, currently 5). Legacy blobs still carrying the old `version` key are read as generation 3, with that value migrated into `run_id` (issue #821). Generation 5 adds `thread` and the per-step `history[].inputs` ledger (issue #872); older snapshots deserialize with empty defaults. |
| `workflow` | string | Name of the active workflow. |
| `step` | string | Current step name. |
| `agent` | string | Agent role executing the current step. |
| `phase` | string | High-level phase (`analysis`, `planning`, `execution`, ...). |
| `history` | HistoryEntry[] | Summarized records of prior steps (see below). |
| `thread` | ThreadRecord[] | The issue's **thread ledger** (issue #872): one classified, id-keyed metadata record per issue artifact (issue body, command, engine output, agent output, human feedback). Reconciled against the full comment list between runs; the prompt's `## Recent thread` is rendered from the relevant records. Body-marker bot-ness is anchored to the engine's own `GET /user` account (or a genuine bot account), so a forged marker cannot reach the trusted flow. |
| `pinned_comment_ids` | int[] | Comment IDs injected **verbatim** into the next agent's context; every other included comment is injected as a summary/excerpt. |
| `current_context` | object | Free-form shared data between agents (pending tasks, constraints, decisions, `last_run_status`, ...). |
| `parent_workflow` | string \| null | Parent workflow name when inside a sub-workflow. Derived view of the second-to-last `position` frame. |
| `parent_workflow_step` | string \| null | Parent step that mounted the sub-workflow. |
| `position` | StepRef[] | The run's full nested call-stack — flat, outermost-first; the last frame is the step actually executing (issue #638). Engine-owned, carried forward across transitions: entry appends a frame, an intra-workflow move replaces the leaf, return-to-parent pops it (issue #812). |
| `issue` | IssueInfo \| null | Static issue metadata: `number`, `url`, `title`, `author`, `repo`. |
| `pull_request` | PullRequestInfo \| null | Same shape as `issue`, for PR contexts. |
| `dispatch` | Dispatch \| null | Outcome + runtime preferences of the current agent (see [Dispatch Object](../../../../../docs/agent-run-config.md#dispatch-object)). |
| `resolution` | StepResolution \| null | Full context of the step-resolution decision (debugging): resolved role, prompt location, matched rule, applied config, timestamp. |
| `gate_decision` | GateDecision \| null | Latest engine-owned human-gate decision (issue #648): `status` (`approved`/`rejected`), `decided_by`, `decided_at`, `feedback`, `comment_id`, and the `position` hold instance it consumes. |
| `gate_history` | GateDecision[] | Append-only audit of every gate decision (issue #648). |

## HistoryEntry

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
| `dispatch` | Dispatch \| null | Routing decision emitted by this step (same shape as the top-level [Dispatch Object](../../../../../docs/agent-run-config.md#dispatch-object)); `null` for legacy entries that predate the field. Makes the history a replayable movement log. |
| `position` | StepRef[] | The mount stack recorded for this step (issue #638). After every run `history[-1].position` equals `WorkflowState.position`; faithful stacks let the terminal unwind recover the ancestor chain one level at a time (issue #812). |
| `metrics` | StateMetrics \| null | Metrics of **this step's** agent invocation (full `Metrics` payload; issue #792). `history[-1].metrics` is the single metrics authority. |
| `inputs` | StepInputRecord[] | The step's materialized deterministic inputs (issue #872): `name`, `binding` (`exec`/`str`/`ref`), `ok`, `detail`, the resolved `value` (capped at `MAX_VALUE_CHARS`), and a `truncated` flag. Empty for legacy entries. |

## ThreadRecord

One metadata-only provenance record per GitHub issue artifact (issue #872) —
the unit the reconciler dedups on and the renderer draws from. Bodies are **not**
copied into state; they stay in the thread.

| Field | Type | Description |
|-------|------|-------------|
| `kind` | string | `issue-body` \| `command` \| `engine-output` \| `agent-output` \| `human-feedback`. The `engine-output` / `agent-output` kinds are derived **only** for a bot author; a non-bot author can only be a `command` or `human-feedback`. |
| `id` | int | GitHub REST id (issue id or comment id). |
| `url` | string | `html_url` permalink. |
| `user_id` | int | Author's GitHub user id. |
| `user_login` | string | Author's login. |
| `user_name` | string | Author's display name (best-effort; REST payloads omit it, so usually empty). |
| `created_at` | string | ISO-8601 creation timestamp. |
| `author_association` | string | GitHub `author_association` (`OWNER`, `MEMBER`, ...). |
| `trusted` | bool | Whether a human-authored record (`command` / `human-feedback`) may be rendered as trusted context (allowlisted association **and** the run's trigger author); untrusted records appear only in the flagged unverified appendix. |
| `deleted` | bool | Set when the id vanishes from the fetched comment list (frozen record kept for audit). |

## StepInputRecord

Persisted form of one materialized step input (issue #872).

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Input name. |
| `binding` | string | Binding kind that produced the value (`exec`/`str`/`ref`). |
| `ok` | bool | Whether materialization succeeded. |
| `detail` | string | Failure detail (empty on success). |
| `value` | string | Resolved value, truncated to `MAX_VALUE_CHARS`. |
| `truncated` | bool | Whether `value` was capped. |

## Annotated Example

```json
{
  "run_id": 1,
  "schema_version": 5,
  "workflow": "default",
  "step": "context",
  "agent": "context",
  "phase": "analysis",
  "history": [
    {"step": "analyze", "agent": "architect", "summary": "Issue analyzed, plan requested", "comment_ids": [1029384], "result": "advance", "dispatch": {"status": "advance", "task_hint": "context", "complexity": "high"}, "inputs": [{"name": "diff", "binding": "exec", "ok": true, "detail": "", "value": "diff --git ...", "truncated": false}], "metrics": {"run_id": "1bb4941e-0bbf-432b-b79c-0b408feea07f", "model_name": "default", "agent_role": "architect", "input_tokens": 8365, "output_tokens": 1143, "max_context_window": 0, "cache_hit_rate": 0.44, "cache_hit_tokens": 3680, "reasoning_tokens": 120, "tool_call_latency_ms": 19685.7, "agent_runtime_ms": 19685.7, "nb_tool_calls": 0, "tool_call_error_rate": 0.0, "review_fix_requests": 0}}
  ],
  "thread": [
    {"kind": "issue-body", "id": 5794187059, "url": "https://github.com/o/r/issues/501", "user_id": 12345, "user_login": "olivier-norture", "user_name": "", "created_at": "2026-10-01T00:00:00Z", "author_association": "OWNER", "trusted": true, "deleted": false},
    {"kind": "agent-output", "id": 1029384, "url": "https://github.com/o/r/issues/501#issuecomment-1029384", "user_id": 99, "user_login": "nebuko[bot]", "user_name": "", "created_at": "2026-10-02T00:00:00Z", "author_association": "NONE", "trusted": false, "deleted": false}
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

## Step Output Fields: Requirements Ledger and Coverage (issue #1257)

Two output fields carry the requirements ledger and its plan coverage as
markdown tables (see [ADR-0016](../../../../../docs/adr/0016-requirements-ledger.md)).

**How to use.** The `analyze` step writes a `requirements` field: a table with
one row per ledger entry — ID (`R<n>` requirement, `K<n>` constraint,
`NG<n>` non-goal, `Q<n>` quality goal), kind, one atomic checkable statement,
a source (short quote or comment link), and an origin (`explicit` or
`inferred`). Mark inferred rows so the human gate can confirm or remove them;
ambiguities become `## Questions` entries with the default you proceed with —
never stop and wait. The `plan` step writes a `coverage` field: one row per
ledger ID with `covered by` (the decision or implementation step that covers
it) and `verified by` (the test name or check). Every R and K must appear;
a row may instead be `deferred: <reason>` (use it only for work genuinely out
of scope, with the reason). Each NG entry is checked as "not violated", and Q
entries rank the options in `## Approaches considered`.

**How it works.** The engine parses the IDs from the `requirements` body of
the analyze output and the `coverage` body of the plan output
(`src/nebuko/engine/ledger.py`). When the plan step's `output.fields`
declares `coverage`, the finalize path compares the enforced IDs (R and K
only — NG and Q are prompt/critique-checked, never refused) against the
covered and deferred rows; a missing ID refuses the finalize in the same turn
with `coverage missing: R3, K1` (sorted, comma-joined), like the no-report
refusal. The check is fail-open: no `coverage` field, no ledger in run state,
or an unparseable ledger (warning only) makes the check a no-op, so default
and legacy workflows are unaffected. Metrics emitted: ledger size, number of
`inferred` entries, and coverage refusals per run
(`LedgerMetricsCollector`).
