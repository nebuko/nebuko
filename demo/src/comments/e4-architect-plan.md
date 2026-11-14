<!-- bot-workflow-terminal -->
<!-- bot-agent-architect -->
<details>
<summary><picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> Workflow State (run 3)</summary>

```json
<!-- WORKFLOW_STATE_START -->
{
  "run_id": 3,
  "schema_version": 4,
  "workflow": "default",
  "step": "plan",
  "agent": "architect",
  "phase": "planning",
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
      "comment_ids": [6117391863],
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
    },
    {
      "step": "context",
      "agent": "context",
      "summary": "## \ud83d\udcda Context Report \u2014 issue #885: `human_gate` on `plan` recorded but not honored\n\nRead-only collection from `/runner/_work/nebuko/nebuko` @ `HEAD` (`0819271ef0a9eabb719309e5dc1178d40db6623b`, \"add .nebuko/workflow/default-with-cmd workflow\"). Facts only \u2014 no analysis or recommendations.\n\n### FILES ",
      "position": [
        {
          "workflow": "default",
          "step": "context"
        }
      ],
      "dispatch": {
        "reason": "no-action default: agent emitted no decision and the AGENT_RUN_CONFIG sentinel could not be parsed (unparseable JSON (Expecting property name enclosed in double quotes at line 1 column 33)); returning to caller 'architect'",
        "origin": "engine_fallback"
      },
      "comment_ids": [6117507100],
      "metrics": {
        "run_id": "a64cc167-f74c-42a2-ad63-3e95dacddbfe",
        "model_name": "default",
        "agent_role": "context",
        "input_tokens": 5408715,
        "output_tokens": 76762,
        "tool_call_latency_ms": 9171.377592086792,
        "agent_runtime_ms": 687853.32,
        "nb_tool_calls": 105,
        "tool_call_error_rate": 1.9230769230769231,
        "complexity": "high",
        "start_time": "2026-10-12T03:49:26.947437",
        "end_time": "2026-10-12T04:01:28.007383+00:00",
        "agent_runtimes_per_call_ms": [22094.14, 19674.36, 3297.36, 3059.32, 3819.81, 17469.48, 22504.77, 24924.68, 5774.53, 27390.89, 25964.24, 4648.89, 6547.34, 3782.11, 2968.03, 5835.13, 4955.74, 3579.49, 6796.08, 3214.52, 6241.17, 3049.9, 21140.82, 5040.59, 10005.49, 2120.28, 15025.83, 22624.61, 3134.42, 5634.79, 2653.97, 5833.06, 3106.44, 2862.01, 25956.14, 5480.36, 9814.52, 1853.95, 8594.4, 5823.67, 3251.43, 3007.23, 9579.77, 1849.53, 15602.99, 12931.76, 2656.25, 33847.82, 43487.66, 16935.37],
        "tool_calls_per_tool": {
          "terminal": 99,
          "file_editor": 4,
          "finish": 1,
          "dispatch_next": 1
        },
        "errors_per_tool": {
          "unknown": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 6568,
            "output": 2265,
            "cache_hit": 0,
            "reasoning": 730
          }
        },
        "tokens_per_second": 158.58832831407935,
        "time_to_first_token": 1.7934141204585448
      }
    },
    {
      "step": "plan",
      "agent": "architect",
      "summary": "## \ud83e\udded Architect Plan \u2014 `human_gate` on `plan` recorded but not honored (issue #885)\n\n<!-- architect-state: phase=plan,status=awaiting_gate -->\n\n### Decision\nHonor a persisted human-gate hold **before** any workflow-edge resolution runs on the state-only bot path. The bug is a single precedence gap: `",
      "position": [
        {
          "workflow": "default",
          "step": "plan"
        }
      ],
      "dispatch": {
        "status": "hold",
        "complexity": "high",
        "reason": "human_gate:plan"
      },
      "metrics": {
        "run_id": "4bb4734c-6d7e-4931-bdbb-2b0379d5f0bf",
        "model_name": "default",
        "agent_role": "architect",
        "input_tokens": 100196,
        "output_tokens": 6398,
        "tool_call_latency_ms": 42568.02034378052,
        "agent_runtime_ms": 170272.08,
        "nb_tool_calls": 3,
        "start_time": "2026-10-12T04:02:08.310764",
        "end_time": "2026-10-12T04:04:59.257883+00:00",
        "agent_runtimes_per_call_ms": [60672.47, 55988.42, 17885.17, 35726.02],
        "tool_calls_per_tool": {
          "dispatch_next": 2,
          "finish": 1
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 26511,
            "output": 2119,
            "cache_hit": 0,
            "reasoning": 155
          }
        },
        "tokens_per_second": 41.360213288406726,
        "time_to_first_token": 2.8122852463275194
      }
    }
  ],
  "current_context": {
    "last_run_status": "success",
    "step_outputs": [
      {
        "step": "analyze",
        "comment_id": "6117391863"
      },
      {
        "step": "context",
        "comment_id": "6117507100"
      },
      {
        "step": "plan"
      }
    ]
  },
  "dispatch": {
    "status": "hold",
    "complexity": "high",
    "reason": "human_gate:plan"
  },
  "resolution": {
    "task": "architect",
    "input_workflow": "default",
    "input_step": "plan",
    "input_complexity": "N/A",
    "role_name": "architect",
    "prompt_location": "/runner/_work/nebuko/nebuko/defaults/prompts/architect.md",
    "agent_selected": "true",
    "selected_workflow": "default",
    "selected_step": "plan",
    "next_step": "implement",
    "reasoning": "none",
    "matched_rule": "workflow:default/step:plan",
    "timestamp": "2026-10-12T04:04:59.259051+00:00"
  },
  "workflow_ref": "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@refs/heads/main",
  "position": [
    {
      "workflow": "default",
      "step": "plan"
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

## 🧭 Architect Plan — `human_gate` on `plan` recorded but not honored (issue #885)

<!-- architect-state: phase=plan,status=awaiting_gate -->

### Decision
Honor a persisted human-gate hold **before** any workflow-edge resolution runs on the state-only bot path. The bug is a single precedence gap: `resolve_config()` routes `prev_state + dispatch_obj` into `_resolve_dispatch_path`, which computes the workflow successor *first* and only falls back to the `hold` status when no successor exists. Since `plan → implement` always resolves, the hold is dead code there (and identically in `_resolve_bot_fallback_path`).

**Root cause (confirmed by the empirical replay in the context report):** `dispatch.status == "hold"` with `reason == "human_gate:plan"` is persisted correctly by `apply_human_gate_intercept`, but the bot re-trigger path (`_resolve_dispatch_path`) never consults `_is_human_gate_hold` — it advances straight to `implement`/dev. The gate *is* honored on the human path (`/nebuko resume` correctly reports the hold; `/nebuko approve gate` / `/nebuko reject gate` correctly route), so the fix is confined to the **bot/auto-dispatch routing paths**, not to gate detection, persistence, or gate-command handling.

### Approaches considered
1. **Gate-hold precedence check at the top of the dispatch/fallback routing paths (chosen).** Reuse the existing `_is_human_gate_hold` predicate (already used by `_resolve_resume_path`) as an early guard in `_resolve_dispatch_path` and `_resolve_bot_fallback_path`, before `resolve_next_from_workflow` is consulted. Minimal, symmetrical, and consistent with the existing `test_hold_outranks_callback` precedence model.
2. **Rewrite `resolution.next_step` at gate-intercept time** (clear it to `""` when converting to `hold`, so no reader can follow the ungated edge). Rejected as sufficient alone: the state-only path deliberately does *not* read `resolution.next_step` (`_resolve_workflow_next_step` docstring), and it would add a semantic mutation to the persisted resolution. Included only as secondary hardening so the persisted state is internally consistent.
3. **Add an explicit `pending_gate` field to schema-v4 `WorkflowState`.** Rejected: `dispatch.status == "hold"` + `reason.startswith("human_gate")` already carries the fact durably (and `human_action_notice.classify()` consumes exactly that shape). A schema bump would be churn without new information.

### Object Design Anchor (the design being approved)
The change lives in `src/nebuko/engine/lib/resolve_config/features/resolve_route/resolve_route.py`. The primary object is the **existing** module-level predicate extended into a routing guard — no new domain class is warranted (the routing module is function-composed; the state it consults is the existing `Dispatch` pydantic model at `src/nebuko/engine/lib/workflow_state/sub_domains/dispatch/dispatch.py`).

- **`_is_human_gate_hold(dispatch: Dispatch | None) -> bool`** — existing predicate (`resolve_route.py:312`); responsibility: *detect that the carried-forward dispatch is a human-gate hold that must block auto-advance*. (Fields consumed: `Dispatch.status: Literal["advance","loop","hold","terminate","soft_kill"]`, `Dispatch.reason: str`.)
- **`_human_gate_hold_result(step: str) -> dict`** — new; one-line responsibility: *produce the `SKIP_AGENT` routing result that keeps the gate step parked until a gate command releases it.*
- Secondary consistency touch: `StepResolution.next_step` (`workflow_state/sub_domains/step_resolution/step_resolution.py:61`) is written as `""` by the gate intercept so the persisted state no longer advertises an ungated forward edge.

**The Object Design Anchor (the `_is_human_gate_hold`-first guard in `_resolve_dispatch_path` / `_resolve_bot_fallback_path`, plus the `next_step` consistency fix) is the design being approved.**

<details>
<summary>Implementation Steps & Object Design Contract</summary>

### Dev Steps
1. **`src/nebuko/engine/lib/resolve_config/features/resolve_route/resolve_route.py`** — in `_resolve_dispatch_path` (~line 602) and `_resolve_bot_fallback_path` (~line 1704): insert the human-gate-hold guard **before** `resolve_next_from_workflow` / the status dispatch chain, so a `human_gate` hold returns `SKIP_AGENT` regardless of whether a successor step resolves. Factor the shared result via `_human_gate_hold_result(step)`. Keep the existing terminal-step `hold` fallback intact for non-gate holds.
2. **`src/nebuko/engine/lib/agent_script/features/build_output/build_output.py`** — in `apply_human_gate_intercept` (~line 390): when converting a completing dispatch to `hold`, ensure `build_state` writes `resolution.next_step = ""` for the intercepted state (clear the ungated edge captured from `WORKFLOW_NEXT` at lines 559-580). Do not mutate other resolution fields.
3. **Regression test — `src/nebuko/engine/lib/test_full_state_routing.py`** — replay the pasted `workflow_state` verbatim (as a new fixture `src/nebuko/engine/lib/fixtures/workflow_state_issue885_gate_hold.json`, modeled on `workflow_state_issue864_context_hold.json`) through `resolve_config()` and assert: bot state-only re-trigger yields `SKIP_AGENT='true'` with a `human_gate` reason and **no** `TASK='dev'`/`WORKFLOW_STEP='implement'`; the engine-sentinel variant behaves identically. Keep the existing approve/reject-gate assertions green (`approved → implement`, `rejected → context`) and `/nebuko resume` still reports the hold.
4. **Unit tests — `src/nebuko/engine/lib/resolve_config/features/resolve_route/test_resolve_route.py`** — add cases next to `test_hold_outranks_callback` / `test_engine_fallback_advance_passes_the_gate`: a `human_gate:*` hold with a resolvable successor must still `SKIP_AGENT` (the exact inversion this fix closes); a non-gate `hold` with a resolvable successor retains current behavior unless the review flags it as also wrong (then extend the same guard to all holds — see open question).
5. **Docs** — `docs/architecture.md` § "The `human_gate` intercept" and `docs/workflows.md` § "Human Gates": one short paragraph each stating that a persisted human-gate hold blocks auto-advance on all bot routing paths until a gate command releases it.

### Object Design Contract

`src/nebuko/engine/lib/resolve_config/features/resolve_route/resolve_route.py`

```python
def _is_human_gate_hold(dispatch: Dispatch | None) -> bool:
    # @stub-protected  (existing; becomes the first guard in the two paths below)
    ...

def _human_gate_hold_result(step: str) -> dict[str, str]:
    # @stub-protected
    # Returns {"SKIP_AGENT": "true", "REASON": "human_gate:<step> held — use /nebuko approve gate or /nebuko reject gate"}
    ...
```

`src/nebuko/engine/lib/agent_script/features/build_output/build_output.py`

```python
def apply_human_gate_intercept(dispatch, workflow_name, workflow_step):
    # @stub-protected  (existing signature, extended contract)
    # Post-condition: when the intercept converts a completing dispatch to hold,
    # build_state records an emptied resolution.next_step for the intercepted state.
    ...
```

`src/nebuko/engine/lib/fixtures/workflow_state_issue885_gate_hold.json` — schema-v4 `WorkflowState` fixture mirroring the pasted state (step `plan`, dispatch `{status: "hold", reason: "human_gate:plan"}`, `resolution.next_step` cleared per step 2).

</details>

**Open question for the gate review:** should the precedence guard apply to *all* `hold` dispatches on the bot path (arguably any hold means "wait for a human"), or strictly `human_gate:*` holds as scoped here? The plan scopes it to `human_gate:*` to keep the blast radius minimal; widening is a one-line change in the same guard.

**Routing decision:** advance to the `plan` human gate for approval, then to @@​dev for implementation at **high** complexity (cross-module engine routing + persistence + regression fixtures).

This plan was created by an AI agent (OpenHands) on behalf of @​olivier-norture.

<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "hold", "complexity": "high", "reason": "human_gate:plan"}} -->