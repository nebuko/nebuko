---
title: "Metrics"
audience: contributor
covers:
  - src/nebuko/engine/lib/metrics_schema.py
  - src/nebuko/engine/lib/agent_metrics.py
  - src/nebuko/engine/lib/human_metrics.py
sidebar_group: "Concepts"
sidebar_label: "Metrics"
sidebar_order: 23
---

# Metrics

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
  `StateMetrics` (see [WORKFLOW_STATE v1](../../../workflow_state/workflow-state-schema.md)).
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
- **Run-time identity (issue #928)** — each run records the *effective* model
  path it used (the complexity-tier-aware ref) in `StateMetrics.model_name`, so
  report-time pricing has a real identity to look up. Run time still writes no
  price; an SDK-reported cost is kept as-is.
- **Issue-close aggregation** — `agent_metrics.py` (GHA job
  `agent-metrics`, triggered on issue close) fetches all comments, reads the
  per-step metrics from each comment's `workflow_state` snapshot (de-duplicating
  entries repeated across accumulating snapshots), aggregates by agent role
  (tokens, runtime, error rate), and posts a single summary table whose last row
  is a bold `Totals` summary row (issue #942). Because a
  subscription provider reports no per-token cost, the job **fills any
  zero-cost entry at report time**: it prices the entry's stored token counts
  against the default price service by its recorded model identity, using the
  same cache-aware math (issue #928). Workflow state is never rewritten —
  rerunning the job recomputes prices on demand. Per-model aggregation
  (`aggregate_by_model`) is still available for future per-model reporting.
  The table's `Avg Latency` and `Avg LLM Runtime` columns display whole seconds
  (`(s)`), while `workflow_state` and `metrics_schema` continue to store both
  metrics in milliseconds; the conversion happens at render time only (issue #947).
- **Reported input is uncached (issue #948)** — the summary's `Input Tokens`
  column reports *new* (uncached) input: the raw prompt count minus its
  cache-read (and cache-write) part, derived per run under the ADR 0014
  token-shape guard, so the figure matches harnesses that report only newly
  processed input. Cache reads remain in `Cache Hit Tokens` (total input is the
  two columns combined) and cost is unaffected.
