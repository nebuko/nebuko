<!-- bot-workflow-terminal --><!-- workflow-continue -->
<!-- bot-agent-dev -->
<details>
<summary><picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> Workflow State (run 4)</summary>

```json
<!-- WORKFLOW_STATE_START -->
{
  "run_id": 4,
  "schema_version": 6,
  "workflow": "default-with-output",
  "step": "implement",
  "agent": "dev",
  "phase": "execution",
  "history": [
    {
      "step": "analyze",
      "agent": "architect",
      "summary": "Context request posted and hand-off to @@\u200bcontext recorded.",
      "position": [
        {
          "workflow": "default-with-output",
          "step": "analyze"
        }
      ],
      "turn_outcome": {
        "status": "completed",
        "appreciation": "approved",
        "note": "Analysis step report: scoped the CI dependency failure and handed fact collection to @@\u200bcontext."
      },
      "prefs": {
        "complexity": "low"
      },
      "comment_ids": [6376624748],
      "metrics": {
        "run_id": "c2ccbdaa-462e-405e-8a23-374c895dc512",
        "model_name": "mistral/zai-glm-5-3-flash",
        "agent_role": "architect",
        "input_tokens": 30067,
        "output_tokens": 3201,
        "reasoning_tokens": 1041,
        "tool_call_latency_ms": 4942.405998706818,
        "agent_runtime_ms": 19769.62,
        "nb_tool_calls": 2,
        "start_time": "2026-10-23T23:14:48.945745",
        "end_time": "2026-10-23T23:15:09.211847+00:00",
        "agent_runtimes_per_call_ms": [8897.15, 2975.5, 6018.16, 1878.81],
        "tool_calls_per_tool": {
          "finalize": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 30067,
            "output": 3201,
            "cache_hit": 0,
            "reasoning": 1041
          }
        },
        "tool_time_per_tool": {
          "finalize": {
            "total_ms": 23.96,
            "count": 2.0
          }
        },
        "tool_runtime_ms": 23.96,
        "wall_clock_ms": 20266.1,
        "tokens_per_second": 271.31428582482187,
        "time_to_first_token": 2.296360702500003
      }
    },
    {
      "step": "context",
      "agent": "context",
      "summary": "Context collection step finalized. The complete report \u2014 covering all six requested fact groups (lint workflow install steps, pyproject dependency declarations, the failing import chain, existing optional-import patterns, PR #1106 diff scope, and other CI jobs' dependency installation) \u2014 has been po",
      "position": [
        {
          "workflow": "default-with-output",
          "step": "context"
        }
      ],
      "turn_outcome": {
        "status": "completed",
        "appreciation": "approved",
        "note": "All six requested fact groups collected verbatim: lint workflow install steps, pyproject dependency/extra declarations, the failing import chain, existing ImportError fallback patterns, PR #1106 diff scope, and the absence of any other PR-check job installing openhands."
      },
      "comment_ids": [6376654915],
      "metrics": {
        "run_id": "27ef0952-ecbe-410d-95de-6d53ca6770e6",
        "model_name": "mistral/zai-glm-5-3-flash",
        "agent_role": "context",
        "input_tokens": 324816,
        "output_tokens": 13749,
        "cache_hit_rate": 0.5319934978572484,
        "cache_hit_tokens": 172800,
        "reasoning_tokens": 2737,
        "tool_call_latency_ms": 5442.499986061683,
        "agent_runtime_ms": 70752.5,
        "nb_tool_calls": 11,
        "complexity": "low",
        "start_time": "2026-10-23T23:15:45.038517",
        "end_time": "2026-10-23T23:17:26.992947+00:00",
        "agent_runtimes_per_call_ms": [4921.15, 3491.31, 3357.27, 2580.78, 2494.63, 2536.25, 2071.74, 6061.15, 2443.8, 18148.04, 4166.4, 14422.51, 4057.48],
        "tool_calls_per_tool": {
          "terminal_batch": 9,
          "finalize": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 324816,
            "output": 13749,
            "cache_hit": 172800,
            "reasoning": 2737
          }
        },
        "tool_time_per_tool": {
          "terminal_batch": {
            "total_ms": 30523.42,
            "count": 9.0
          },
          "finalize": {
            "total_ms": 39.66,
            "count": 2.0
          }
        },
        "tool_runtime_ms": 30563.08,
        "wall_clock_ms": 101954.43,
        "tokens_per_second": 399.60273567677785,
        "time_to_first_token": 2.5579193998461554
      }
    },
    {
      "step": "plan",
      "agent": "architect",
      "summary": "The plan is finalized and approved: fix the lint-job failure by guarding the unconditional `openhands.sdk.get_logger` import in `src/nebuko/engine/git_session.py` with the repo's established `try/except ImportError` fallback (issue #928 precedent), keeping `openhands-sdk` out of the default environm",
      "position": [
        {
          "workflow": "default-with-output",
          "step": "plan"
        }
      ],
      "turn_outcome": {
        "status": "completed",
        "appreciation": "approved",
        "note": "Plan complete: guard the pre-existing openhands import in git_session.py per repo precedent instead of adding the SDK to the lint environment."
      },
      "prefs": {
        "complexity": "low"
      },
      "comment_ids": [6376668019],
      "metrics": {
        "run_id": "b1b3be2b-ae11-4954-91a2-fa3ed413bc91",
        "model_name": "mistral/zai-glm-5-3",
        "agent_role": "architect",
        "input_tokens": 69138,
        "output_tokens": 4214,
        "max_context_window": 1048576,
        "cache_hit_rate": 0.5591136567444821,
        "cache_hit_tokens": 38656,
        "reasoning_tokens": 1022,
        "cost": 0.06662823999999999,
        "tool_call_latency_ms": 5390.32381772995,
        "agent_runtime_ms": 21561.3,
        "nb_tool_calls": 2,
        "start_time": "2026-10-23T23:18:09.596746",
        "end_time": "2026-10-23T23:18:31.697978+00:00",
        "agent_runtimes_per_call_ms": [15151.98, 995.02, 4464.56, 949.73],
        "tool_calls_per_tool": {
          "finalize": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 69138,
            "output": 4214,
            "cache_hit": 38656,
            "reasoning": 1022
          }
        },
        "tool_time_per_tool": {
          "finalize": {
            "total_ms": 32.27,
            "count": 2.0
          }
        },
        "tool_runtime_ms": 32.27,
        "wall_clock_ms": 22101.23,
        "tokens_per_second": 300.54601858027763,
        "time_to_first_token": 0.9389381239999999
      },
      "complexity_adjustment": {
        "delta": 1,
        "trigger": "evidence-drift",
        "evidence": {
          "llm": {
            "input_tokens": 324816.0,
            "output_tokens": 13749.0,
            "nb_tool_calls": 11.0,
            "cost": 0.0,
            "tool_call_error_rate": 0.0,
            "review_fix_requests": 0.0,
            "dispatch_recall_count": 0.0
          }
        },
        "rationale": "evidence drift low -> high"
      }
    },
    {
      "step": "implement",
      "agent": "dev",
      "summary": "Final report posted: the `openhands` import in `src/nebuko/engine/git_session.py` is now guarded with the repo's standard fallback pattern, lint-config completes in the base-deps-only lint environment, and the fix is published on PR #1133 (fixes #1129).",
      "position": [
        {
          "workflow": "default-with-output",
          "step": "implement"
        }
      ],
      "turn_outcome": {
        "status": "completed",
        "appreciation": "approved",
        "note": "Import guard implemented per plan, lint-config verified in a base-deps-only venv, work published on PR #1133."
      },
      "metrics": {
        "run_id": "28c8f3a1-7c4f-4229-8ace-48d13c024364",
        "model_name": "mistral/zai-glm-5-3-flash",
        "agent_role": "dev",
        "input_tokens": 687677,
        "output_tokens": 7603,
        "cache_hit_rate": 0.8359273321632104,
        "cache_hit_tokens": 574848,
        "reasoning_tokens": 2942,
        "tool_call_latency_ms": 2457.67734448115,
        "agent_runtime_ms": 88476.38,
        "nb_tool_calls": 36,
        "tool_call_error_rate": 5.555555555555555,
        "complexity": "medium",
        "start_time": "2026-10-23T23:32:37.286992",
        "end_time": "2026-10-23T23:39:02.720299+00:00",
        "agent_runtimes_per_call_ms": [1868.56, 1447.24, 1584.17, 1915.81, 1463.37, 2304.98, 2638.36, 1996.46, 1872.61, 2196.39, 1617.24, 2242.73, 1797.3, 2060.51, 1493.18, 2470.9, 1435.16, 1517.06, 1981.46, 3168.18, 3259.76, 5824.84, 3350.23, 1827.71, 3474.0, 3395.87, 1848.39, 2062.72, 4313.14, 2280.42, 2244.55, 3649.47, 3326.33, 1652.8, 3945.44, 2949.04],
        "tool_calls_per_tool": {
          "invoke_skill": 1,
          "terminal_batch": 30,
          "file_editor": 2,
          "finalize": 3
        },
        "errors_per_tool": {
          "terminal_batch": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 687677,
            "output": 7603,
            "cache_hit": 574848,
            "reasoning": 2942
          }
        },
        "tool_time_per_tool": {
          "invoke_skill": {
            "total_ms": 6.3,
            "count": 1.0
          },
          "terminal_batch": {
            "total_ms": 296371.24,
            "count": 30.0
          },
          "file_editor": {
            "total_ms": 158.91,
            "count": 2.0
          },
          "finalize": {
            "total_ms": 21.71,
            "count": 3.0
          }
        },
        "tool_runtime_ms": 296558.16,
        "wall_clock_ms": 385433.31,
        "publication_guard": "recalled_ok",
        "tokens_per_second": 308.6012845061697,
        "time_to_first_token": 1.6005463028055615
      }
    }
  ],
  "thread": [
    {
      "kind": "issue-body",
      "id": 6006831837,
      "url": "https://github.com/nebuko/nebuko/issues/1129",
      "user_id": 11475739,
      "user_login": "olivier-norture",
      "created_at": "2026-10-23T23:13:46Z",
      "author_association": "MEMBER",
      "trusted": true
    },
    {
      "kind": "command",
      "id": 6376610071,
      "url": "https://github.com/nebuko/nebuko/issues/1129#issuecomment-6376610071",
      "user_id": 11475739,
      "user_login": "olivier-norture",
      "created_at": "2026-10-23T23:14:03Z",
      "author_association": "MEMBER",
      "trusted": true
    },
    {
      "kind": "engine-output",
      "id": 6376615521,
      "url": "https://github.com/nebuko/nebuko/issues/1129#issuecomment-6376615521",
      "user_id": 11475739,
      "user_login": "olivier-norture",
      "created_at": "2026-10-23T23:14:28Z",
      "author_association": "MEMBER",
      "trusted": true
    },
    {
      "kind": "engine-output",
      "id": 6376619887,
      "url": "https://github.com/nebuko/nebuko/issues/1129#issuecomment-6376619887",
      "user_id": 11475739,
      "user_login": "olivier-norture",
      "created_at": "2026-10-23T23:14:48Z",
      "author_association": "MEMBER",
      "trusted": true
    },
    {
      "kind": "agent-output",
      "id": 6376624748,
      "url": "https://github.com/nebuko/nebuko/issues/1129#issuecomment-6376624748",
      "user_id": 11475739,
      "user_login": "olivier-norture",
      "created_at": "2026-10-23T23:15:10Z",
      "author_association": "MEMBER",
      "trusted": true
    },
    {
      "kind": "engine-output",
      "id": 6376632304,
      "url": "https://github.com/nebuko/nebuko/issues/1129#issuecomment-6376632304",
      "user_id": 11475739,
      "user_login": "olivier-norture",
      "created_at": "2026-10-23T23:15:43Z",
      "author_association": "MEMBER",
      "trusted": true
    },
    {
      "kind": "agent-output",
      "id": 6376654915,
      "url": "https://github.com/nebuko/nebuko/issues/1129#issuecomment-6376654915",
      "user_id": 11475739,
      "user_login": "olivier-norture",
      "created_at": "2026-10-23T23:17:27Z",
      "author_association": "MEMBER",
      "trusted": true
    }
  ],
  "current_context": {
    "last_run_status": "success",
    "step_outputs": [
      {
        "step": "analyze",
        "comment_id": "6376624748"
      },
      {
        "step": "context",
        "comment_id": "6376654915"
      },
      {
        "step": "plan",
        "comment_id": "6376668019"
      },
      {
        "step": "implement"
      }
    ],
    "publication_guard": "recalled_ok",
    "publication_guard_detail": "unpushed_commits"
  },
  "turn_outcome": {
    "status": "completed",
    "appreciation": "approved",
    "note": "Import guard implemented per plan, lint-config verified in a base-deps-only venv, work published on PR #1133."
  },
  "gate_decision": {
    "status": "approved",
    "workflow": "default-with-output",
    "target_step": "plan",
    "decided_by": "olivier-norture",
    "decided_at": "2026-10-23T23:32:31.256804Z",
    "comment_id": 6376827163,
    "position": [
      {
        "workflow": "default-with-output",
        "step": "plan"
      }
    ]
  },
  "gate_history": [
    {
      "status": "approved",
      "workflow": "default-with-output",
      "target_step": "plan",
      "decided_by": "olivier-norture",
      "decided_at": "2026-10-23T23:32:31.256804Z",
      "comment_id": 6376827163,
      "position": [
        {
          "workflow": "default-with-output",
          "step": "plan"
        }
      ]
    }
  ],
  "gate": {
    "step": "plan",
    "status": "released"
  },
  "resolution": {
    "task": "dev",
    "input_workflow": "default-with-output",
    "input_step": "implement",
    "input_complexity": "medium",
    "role_name": "dev",
    "prompt_location": "/home/runner/work/nebuko/nebuko/defaults/prompts/dev.md",
    "agent_selected": "true",
    "selected_workflow": "default-with-output",
    "selected_step": "implement",
    "next_step": "review",
    "reasoning": "none",
    "matched_rule": "workflow:default-with-output/step:implement",
    "timestamp": "2026-10-23T23:39:03.190517+00:00"
  },
  "workflow_ref": "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@refs/heads/main",
  "position": [
    {
      "workflow": "default-with-output",
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

# Guard `openhands` import in `git_session.py` — lint-config now runs on base deps

## Changes
<!-- nebuko:section changes -->
- `src/nebuko/engine/git_session.py`: wrapped the module-level `from openhands.sdk import get_logger` in the repo's established fallback pattern (same as `load_price_table.py`, issue #928): `try/except ImportError` falling back to `get_logger = logging.getLogger`, with `import logging` added to the stdlib block. No new dependency — `openhands-sdk` stays optional per the `runtime` extra design.
- Branch `feature/issue-1129-guard-openhands-import` (rebased on `origin/main` @ `9add874a`), PR: https://github.com/nebuko/nebuko/pull/1133 (fixes #1129).
- Publication state re-verified after the "unpublished work" warning: the commit (`7533860e`) is pushed and PR #1133 is OPEN; the warning was a stale local upstream tracking `origin/main`, now corrected to track `origin/feature/issue-1129-guard-openhands-import`.
<!-- /nebuko:section -->

## Verification
<!-- nebuko:section verification -->
- Reproduced the exact lint-job environment (venv with only `pyyaml pydantic`): `PYTHONPATH=src python -m nebuko.engine lint-config defaults/agents/` now completes — no `ModuleNotFoundError`. The 15 remaining `[C3-unknown-tool]` errors are pre-existing on `main` (verified by stashing) and unrelated to this issue.
- `tests/engine/test_git_session.py`: 18 passed.
- Full `tests/engine/`: 1072 passed, 9 failed — all 9 failures (`test_finalize_wiring.py`, `test_exec_chain_wiring.py`, `test_agent_host.py`, `test_config_lint_cli.py`) reproduce identically on the base commit `1e9f146d`, so they are pre-existing and unrelated.
<!-- /nebuko:section -->