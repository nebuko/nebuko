The human_gate on plan step was ignored by the engine whereas the gate was correctly applied in the workflow_state.
The workflow move to implementation right after architect agent sent is plan.

It happenned in #885.

Here is the architect plan workflow_state; you can build a test to reproduce the situation:
<details>

```json
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
      "summary": "## \ud83e\udded Architect Analysis \u2014 `exec`/`script` steps must post GitHub comments\n\n### Problem\nWorkflow steps that run a command (`script`/`exec`, e.g. `git_diff`, `run_tests`) execute and produce output, but do **not** publish a GitHub comment the way agent steps do. Because downstream `inputs: [...]` wiri",
      "position": [
        {
          "workflow": "default",
          "step": "analyze"
        }
      ],
      "dispatch": {
        "task_hint": "context",
        "complexity": "high",
        "reason": "Fresh issue; delegate high-depth context collection on engine run_loop, comment posting, step schema, and input/output wiring before designing."
      },
      "comment_ids": [6112231258],
      "metrics": {
        "run_id": "f4f6fa06-dcb1-4c61-a7fc-e3d988f257a0",
        "model_name": "default",
        "agent_role": "architect",
        "input_tokens": 27305,
        "output_tokens": 4593,
        "tool_call_latency_ms": 10350.531180699667,
        "agent_runtime_ms": 31051.59,
        "nb_tool_calls": 4,
        "start_time": "2026-10-11T18:25:12.664410",
        "end_time": "2026-10-11T18:25:44.437159+00:00",
        "agent_runtimes_per_call_ms": [13957.93, 4944.1, 12149.56],
        "tool_calls_per_tool": {
          "dispatch_next": 2,
          "finish": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 9037,
            "output": 1952,
            "cache_hit": 0,
            "reasoning": 973
          }
        },
        "tokens_per_second": 177.4858049717276,
        "time_to_first_token": 1.4716875778976828
      }
    },
    {
      "step": "context",
      "agent": "context",
      "summary": "## \ud83d\udcda Context Report \u2014 Issue #885: `exec`/`script` steps must post GitHub comments\n\nRead-only collection from `/runner/_work/nebuko/nebuko` @ `HEAD detached at 0819271`. Facts only \u2014 no analysis or recommendations.\n\n### FILES FOUND\n\n**Agent-step comment posting path**\n- `src/nebuko/engine/lib/prepare",
      "position": [
        {
          "workflow": "default",
          "step": "context"
        }
      ],
      "dispatch": {
        "reason": "no-action default: agent emitted no decision; returning to caller 'architect'",
        "origin": "engine_fallback"
      },
      "comment_ids": [6112286895],
      "metrics": {
        "run_id": "cdec6c66-a69c-4d87-a4db-053adfc3372f",
        "model_name": "default",
        "agent_role": "context",
        "input_tokens": 4918800,
        "output_tokens": 39835,
        "tool_call_latency_ms": 4441.396597659949,
        "agent_runtime_ms": 293132.18,
        "nb_tool_calls": 107,
        "tool_call_error_rate": 0.9433962264150944,
        "complexity": "high",
        "start_time": "2026-10-11T18:26:26.064079",
        "end_time": "2026-10-11T18:31:49.535317+00:00",
        "agent_runtimes_per_call_ms": [3256.36, 2585.63, 2935.75, 3278.98, 2794.98, 3925.16, 3528.96, 2838.09, 3945.51, 3712.29, 2726.05, 3085.14, 3779.08, 3798.19, 2777.16, 3803.26, 2328.72, 3911.78, 2844.27, 8327.71, 3909.5, 2855.23, 3268.39, 3892.7, 3054.22, 2207.97, 3200.13, 2579.68, 3061.15, 2487.91, 3960.87, 4745.4, 3434.1, 2661.26, 4331.14, 2641.7, 4665.03, 3085.99, 4606.41, 2753.68, 4098.8, 3689.85, 3643.47, 4012.2, 5123.48, 3845.41, 10173.18, 36978.61, 34729.08, 4402.95],
        "tool_calls_per_tool": {
          "terminal": 82,
          "file_editor": 23,
          "finish": 1,
          "dispatch_next": 1
        },
        "errors_per_tool": {
          "unknown": 1
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 6752,
            "output": 823,
            "cache_hit": 0,
            "reasoning": 76
          }
        },
        "tokens_per_second": 214.1836184710011,
        "time_to_first_token": 1.6873932969147483
      }
    },
    {
      "step": "plan",
      "agent": "architect",
      "summary": "## \ud83e\udded Architect Plan \u2014 `exec`/`script` steps must post GitHub comments (issue #885)\n\n<!-- architect-state: phase=plan,status=awaiting_gate -->\n\n### Decision\nMake `run_script` steps full participants in the comment lifecycle: a **\"command started\"** notice on entry and a **state-carrying \"command succ",
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
        "run_id": "55b5f7ac-e6a7-4043-9810-6d74539733ec",
        "model_name": "default",
        "agent_role": "architect",
        "input_tokens": 127536,
        "output_tokens": 21908,
        "tool_call_latency_ms": 217833.5095246633,
        "agent_runtime_ms": 653500.53,
        "nb_tool_calls": 3,
        "start_time": "2026-10-11T18:32:29.523721",
        "end_time": "2026-10-11T18:43:23.870405+00:00",
        "agent_runtimes_per_call_ms": [594484.55, 16505.03, 42510.95],
        "tool_calls_per_tool": {
          "dispatch_next": 2,
          "finish": 1
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 48076,
            "output": 3573,
            "cache_hit": 0,
            "reasoning": 400
          }
        },
        "tokens_per_second": 59.755344336658005,
        "time_to_first_token": 4.282628062258785
      }
    }
  ],
  "current_context": {
    "last_run_status": "success",
    "step_outputs": [
      {
        "step": "analyze",
        "comment_id": "6112231258"
      },
      {
        "step": "context",
        "comment_id": "6112286895"
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
    "timestamp": "2026-10-11T18:43:23.871819+00:00"
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
```

</details>