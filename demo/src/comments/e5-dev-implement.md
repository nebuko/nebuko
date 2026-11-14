<!-- bot-workflow-terminal -->
<!-- bot-agent-dev -->
<details>
<summary><picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> Workflow State (run 4)</summary>

```json
<!-- WORKFLOW_STATE_START -->
{
  "run_id": 4,
  "schema_version": 4,
  "workflow": "default",
  "step": "implement",
  "agent": "dev",
  "phase": "execution",
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
      "comment_ids": [6117540595],
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
    },
    {
      "step": "implement",
      "agent": "dev",
      "summary": "## \ud83d\udee0\ufe0f Dev Report \u2014 `human_gate` on `plan` recorded but not honored (issue #887)\n\n### Outcome\nThe approved architect plan was already implemented on the open PR **#906** (`feature/issue-887-human-gate-hold-precedence-v2`, *\"fix(#887): honor human_gate hold precedence over workflow routing (round 2)\"*",
      "position": [
        {
          "workflow": "default",
          "step": "implement"
        }
      ],
      "dispatch": {
        "status": "hold",
        "reason": "no-action default: agent emitted no decision",
        "origin": "engine_fallback"
      },
      "metrics": {
        "run_id": "1150502f-3bfc-4e2f-87e5-689bd46e2687",
        "model_name": "default",
        "agent_role": "dev",
        "input_tokens": 2837110,
        "output_tokens": 53849,
        "tool_call_latency_ms": 13385.30022022771,
        "agent_runtime_ms": 682650.31,
        "nb_tool_calls": 70,
        "tool_call_error_rate": 1.4492753623188406,
        "complexity": "low",
        "start_time": "2026-10-12T19:07:11.423685",
        "end_time": "2026-10-12T19:25:02.977371+00:00",
        "agent_runtimes_per_call_ms": [5501.99, 5424.9, 4002.9, 3832.42, 6606.19, 7932.55, 15121.62, 10516.01, 21806.75, 5854.88, 17921.59, 20321.29, 20669.13, 27156.56, 20194.54, 14569.91, 23799.36, 13840.22, 20992.18, 4171.71, 13810.37, 27633.64, 24962.7, 10205.44, 2936.82, 3975.97, 32565.06, 48717.26, 57276.91, 34851.31, 6542.22, 2652.23, 10109.24, 7793.71, 11248.04, 2391.48, 3576.92, 5023.95, 2829.85, 7540.88, 9295.86, 8730.73, 3214.35, 5242.62, 34924.94, 2824.85, 2386.42, 4849.3, 10218.54, 9768.2],
        "tool_calls_per_tool": {
          "terminal": 68,
          "finish": 1,
          "dispatch_next": 1
        },
        "errors_per_tool": {
          "unknown": 1
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 72755,
            "output": 903,
            "cache_hit": 3840,
            "reasoning": 19
          }
        },
        "tokens_per_second": 105.84115666046164,
        "time_to_first_token": 1.3578897864037358
      },
      "complexity_adjustment": {
        "delta": -1,
        "trigger": "evidence-drift",
        "evidence": {
          "llm": {
            "input_tokens": 100196.0,
            "output_tokens": 6398.0,
            "nb_tool_calls": 3.0,
            "cost": 0.0,
            "tool_call_error_rate": 0.0,
            "review_fix_requests": 0.0,
            "dispatch_recall_count": 0.0
          }
        },
        "rationale": "evidence drift high -> low"
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
        "step": "plan",
        "comment_id": "6117540595"
      },
      {
        "step": "implement"
      }
    ]
  },
  "dispatch": {
    "status": "hold",
    "reason": "no-action default: agent emitted no decision",
    "origin": "engine_fallback"
  },
  "gate_decision": {
    "status": "approved",
    "workflow": "default",
    "target_step": "plan",
    "decided_by": "olivier-norture",
    "decided_at": "2026-10-12T19:06:56.967500Z",
    "comment_id": 6130801891,
    "position": [
      {
        "workflow": "default",
        "step": "plan"
      }
    ]
  },
  "gate_history": [
    {
      "status": "approved",
      "workflow": "default",
      "target_step": "plan",
      "decided_by": "olivier-norture",
      "decided_at": "2026-10-12T19:06:56.967500Z",
      "comment_id": 6130801891,
      "position": [
        {
          "workflow": "default",
          "step": "plan"
        }
      ]
    }
  ],
  "resolution": {
    "task": "dev",
    "input_workflow": "default",
    "input_step": "implement",
    "input_complexity": "low",
    "role_name": "dev",
    "prompt_location": "/runner/_work/nebuko/nebuko/defaults/prompts/dev.md",
    "agent_selected": "true",
    "selected_workflow": "default",
    "selected_step": "implement",
    "next_step": "review",
    "reasoning": "none",
    "matched_rule": "workflow:default/step:implement",
    "timestamp": "2026-10-12T19:25:03.444529+00:00"
  },
  "workflow_ref": "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@refs/heads/main",
  "position": [
    {
      "workflow": "default",
      "step": "implement"
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

*Developer answer*

## 🛠️ Dev Report — `human_gate` on `plan` recorded but not honored (issue #887)

### Outcome
The approved architect plan was already implemented on the open PR **#906** (`feature/issue-887-human-gate-hold-precedence-v2`, *"fix(#887): honor human_gate hold precedence over workflow routing (round 2)"*). My work in the `implement` step was to bring that branch current and verify it end-to-end.

### Actions
1. **Rebased onto current `origin/main`.** The branch base predated two `main` commits (notably `#908`, which removed the workflow-trigger mechanism). The shallow/grafted clone made a plain `git rebase origin/main` mis-identify the old `main` tip `c15af38b` as an unrelated root, so I replayed only the two feature commits with `git rebase --onto origin/main c15af38b`. Clean, conflict-free. PR #906 now reports `MERGEABLE` / mergeStateStatus `CLEAN`.
2. **Verified the implementation against the Object Design Contract:**
   - `resolve_route.py` — `_is_human_gate_hold`-first guard plus `_human_gate_hold_result` / `_gate_step_from_reason`, applied in `_resolve_dispatch_path`, `_resolve_bot_fallback_path`, and `_resolve_handoff_decision` (closing the extra `function_call` bypass found in round 2).
   - `build_output.py` — `_dispatch_is_human_gate_hold`; the gate intercept empties `resolution.next_step` and drops any persisted agent-named `function_call` on the held state.
   - Regression fixture `workflow_state_issue885_gate_hold.json` (verbatim replay of the reported state) plus unit tests in `test_full_state_routing.py`, `test_resolve_route.py`, and `test_build_output.py`.
   - Docs updated in `docs/architecture.md` and `docs/workflows.md`.
3. **Force-pushed** the rebased branch to PR #906 (force required due to the history rewrite).

### Verification
- All **14 new #887 tests pass** (gate-hold precedence, engine-sentinel variant, persisted-`function_call` case, non-gate-hold out-of-scope case).
- Full suite (`pytest tests src/nebuko/engine/lib`): **2749 passed, 3 skipped**. The 17 failures are **byte-identical to a clean `origin/main` baseline** (pre-existing environmental issues: the `.nebuko/` overlay shadowing `defaults/workflows` plus config-path assumptions), confirmed by diffing the sorted failure sets — only worktree path strings differ.

### Notes
- The behavior fix: a persisted `human_gate` hold now blocks auto-advance on **every** bot routing path (dispatch, state-only fallback, and `function_call` hand-off) until an identity-verified `/nebuko approve gate` / `/nebuko reject gate` releases it, so the stale `plan → implement` edge can no longer route past the gate.
- The architect's open question (whether to widen the guard to *all* holds) is implemented as scoped — strictly `human_gate:*` — per the approved plan; a plain hold with a resolvable successor still advances (covered by an explicit test).

Changes submitted in PR #906.

<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "hold", "reason": "no-action default: agent emitted no decision"}} -->