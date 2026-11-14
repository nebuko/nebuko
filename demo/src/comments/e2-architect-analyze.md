<!-- bot-workflow-terminal -->
<!-- bot-agent-architect -->
<!-- workflow-continue -->
<details>
<summary><picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> Workflow State (run 1)</summary>

```json
<!-- WORKFLOW_STATE_START -->
{
  "run_id": 1,
  "schema_version": 4,
  "workflow": "default",
  "step": "analyze",
  "agent": "architect",
  "phase": "analysis",
  "history": [
    {
      "step": "analyze",
      "agent": "architect",
      "summary": "## \ud83e\udded Architect Analysis \u2014 human_gate on the `plan` step is recorded but not honored (issue #885)\n\n### Summary of the bug\nThe `human_gate: true` on the `plan` step did **not** block execution: the run advanced to `implement` immediately after the architect posted its plan. The gate *was* recognized \u2014",
      "position": [
        {
          "workflow": "default",
          "step": "analyze"
        }
      ],
      "dispatch": {
        "task_hint": "context",
        "complexity": "high",
        "reason": "Fresh bug report; delegate high-depth context collection on human_gate handling, resolution/gate ordering, resume routing, workflow YAML, and persistence before planning a fix."
      },
      "metrics": {
        "run_id": "00df6a6b-5a53-411d-8dac-ee1a5634acd0",
        "model_name": "default",
        "agent_role": "architect",
        "input_tokens": 33415,
        "output_tokens": 5354,
        "tool_call_latency_ms": 15078.480323155722,
        "agent_runtime_ms": 45235.44,
        "nb_tool_calls": 4,
        "start_time": "2026-10-12T03:47:56.233639",
        "end_time": "2026-10-12T03:48:42.014167+00:00",
        "agent_runtimes_per_call_ms": [25392.09, 7492.05, 12351.3],
        "tool_calls_per_tool": {
          "dispatch_next": 2,
          "finish": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 11679,
            "output": 1459,
            "cache_hit": 0,
            "reasoning": 107
          }
        },
        "tokens_per_second": 133.36486261136272,
        "time_to_first_token": 1.524595326045528
      }
    }
  ],
  "current_context": {
    "last_run_status": "success",
    "step_outputs": [
      {
        "step": "analyze"
      }
    ]
  },
  "dispatch": {
    "task_hint": "context",
    "complexity": "high",
    "reason": "Fresh bug report; delegate high-depth context collection on human_gate handling, resolution/gate ordering, resume routing, workflow YAML, and persistence before planning a fix."
  },
  "resolution": {
    "task": "architect",
    "input_workflow": "default",
    "input_step": "analyze",
    "input_complexity": "N/A",
    "role_name": "architect",
    "prompt_location": "/runner/_work/nebuko/nebuko/defaults/prompts/architect.md",
    "agent_selected": "true",
    "selected_workflow": "default",
    "selected_step": "analyze",
    "next_step": "context",
    "reasoning": "none",
    "matched_rule": "workflow:default/step:analyze",
    "timestamp": "2026-10-12T03:48:42.411481+00:00"
  },
  "function_call": {
    "agent_name": "context",
    "complexity": "high"
  },
  "workflow_ref": "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@refs/heads/main",
  "position": [
    {
      "workflow": "default",
      "step": "analyze"
    }
  ],
  "complexity_policy": {
    "bands": {
      "low": {
        "min_score": 1,
        "guidance": "LOW \u2014 localized, single-concern change: one file or a tight cluster, no design ambiguity, no cross-module contract change, no open questions. If it fits in a single focused edit, it is LOW."
      },
      "medium": {
        "min_score": 1,
        "guidance": "MEDIUM \u2014 the default working tier: a bounded multi-file change with a clear approach, a couple of touch points, no unresolved design choice. Most feature work and bug fixes belong here."
      },
      "high": {
        "min_score": 3,
        "any_of": ["multi-issue-linkage", "reopened-issue", "high-token-burn", "large-scope", "human-friction"],
        "guidance": "HIGH \u2014 reserved for genuinely heavy work: multiple linked issues or reopened problem, cross-module/architectural change, sustained large context burn, or repeated human change-requests. Reach for HIGH only when the evidence below actually fires; an unproven HIGH erodes the cost/value of the tier."
      }
    },
    "signal_rules": [
      {
        "signal": "multi-issue-linkage",
        "family": "context",
        "field": "linked_issues",
        "threshold": 2.0
      },
      {
        "signal": "reopened-issue",
        "family": "context",
        "field": "reopened",
        "threshold": 1.0
      },
      {
        "signal": "high-token-burn",
        "family": "llm",
        "field": "input_tokens",
        "threshold": 150000.0
      },
      {
        "signal": "large-scope",
        "family": "llm",
        "field": "nb_tool_calls",
        "threshold": 40.0
      },
      {
        "signal": "human-friction",
        "family": "human",
        "field": "changes_requested",
        "threshold": 2.0
      }
    ],
    "target_distribution": {
      "low": 0.35,
      "medium": 0.5,
      "high": 0.15
    }
  }
}
<!-- WORKFLOW_STATE_END -->
```
</details>

*Architect answer*

## 🧭 Architect Analysis — human_gate on the `plan` step is recorded but not honored (issue #885)

### Summary of the bug
The `human_gate: true` on the `plan` step did **not** block execution: the run advanced to `implement` immediately after the architect posted its plan. The gate *was* recognized — it appears correctly in the persisted `workflow_state` — yet routing ignored it.

### Evidence from the pasted `workflow_state`
The state is internally self-contradictory, which narrows the failure to the routing/resume path rather than gate detection:

- `history[plan].dispatch` = `{"status": "hold", "complexity": "high", "reason": "human_gate:plan"}` — the gate **was** produced and recorded.
- Top-level `dispatch` mirrors the same hold.
- **But** `resolution.next_step = "implement"`, `resolution.selected_step = "plan"`, `resolution.matched_rule = "workflow:default/step:plan"`, `resolution.reasoning = "none"`.
- `current_context.step_outputs` shows the `plan` step with **no `comment_id`** — i.e. the plan step never posted, consistent with the gate interception happening on a path where the plan comment was not published.

So the hold is persisted, but the decision that actually drives the next run (`resolution.next_step`) was computed **as if the step were ungated**, and that stale value won on resume.

### Leading hypotheses (ranked, to be confirmed by context — not yet assumed)
1. **Ordering / stale `resolution` (primary suspect).** Per my own role contract, a gated step ends by emitting `dispatch_next(status='advance')`, and the *engine* intercepts that `advance` to record the `human_gate:plan` hold. If `resolution.next_step` is computed/persisted **before** the gate interception, the run resumes by following `resolution.next_step = implement` while the holding `dispatch` is never consulted.
2. **Resume path reads the wrong field.** The resume branch may select the target step from `resolution.next_step` / `position` / rule matching rather than checking for a pending human gate, so `dispatch.status == "hold"` is ignored.
3. **No explicit pending-gate flag in schema-v4 state.** The "awaiting human approval" condition may live only in the transient `dispatch.reason` string (`human_gate:plan`) that the router does not inspect, so nothing durable blocks the advance.
4. **Fallback dispatch bypasses the gate.** The architect step recorded `dispatch_next: 2`; an engine fallback/no-action path may have supplied a default `advance` that skipped the gate interception entirely.

### Why this is a correctness bug, not a prompt issue
A gated step can silently self-approve, defeating the single human approval the workflow promises (`/nebuko approve gate`). Any `human_gate: true` step is affected, and the failure is deterministic from the persisted state, so it is directly reproducible from the pasted JSON.

### Context requested from @@​context (read-only)
Delegated at **high** complexity, since engine control flow, the schema-v4 persistence, and routing all intersect:

1. **Gate handling in the run loop** — where `dispatch_next` results are interpreted; how `advance` / `hold` / `loop` are dispatched; where the literal `human_gate:` / `human_gate:plan` is produced and consumed; the exact code that intercepts `advance` on a `human_gate: true` step.
2. **Resolution vs gate ordering** — the code that builds the `resolution` object (`next_step`, `selected_step`, `matched_rule`, `reasoning`) and exactly where it is written relative to the gate interception. Confirm whether `next_step` is computed **before** the hold is recorded.
3. **Resume path** — how a persisted `workflow_state` is loaded and how the *next* run chooses its step: does it consult `resolution.next_step`, `dispatch.status`, `current_context`, or `position`? Quote the branch that picks the target step.
4. **Workflow definition** — the `default` workflow YAML: the `plan` step's `human_gate` field and the `plan → implement` transition / `inputs` wiring.
5. **Persistence** — the schema-version-4 `workflow_state` serialization: which fields survive a run, whether any explicit "pending human gate / awaiting approval" flag exists, and how `dispatch.status == "hold"` is read back on load.
6. **Tests & fixtures** — existing tests covering `human_gate`, hold/resume routing, and any helper that constructs a `workflow_state` fixture (so a regression test can reproduce the pasted state exactly).
7. **Pointers** — file paths, symbol names, and line numbers for each of the above, with the relevant code snippets quoted verbatim.

### Next step
Once @@​context returns with the quoted control-flow and persistence details, I will emit the single gated planning artifact (Architecture Summary + Object Design Anchor, then Dev Steps & Object Design Contract in a collapsed `<details>` block) that pins the fix to a concrete class/method and adds a regression test reproducing the pasted `workflow_state`.

**Routing decision:** advance to `context` at **high** complexity for read-only collection.

<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "complexity": "high", "reason": "Fresh bug report; delegate high-depth context collection on human_gate handling, resolution/gate ordering, resume routing, workflow YAML, and persistence before planning a fix.", "agent": "context"}, "function_call": {"function": "call_agent", "agent_name": "context", "complexity": "high"}} -->