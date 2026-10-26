<!-- bot-workflow-terminal --><!-- workflow-continue -->
<!-- bot-agent-architect -->
<details>
<summary><picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> Workflow State (run 3)</summary>

```json
<!-- WORKFLOW_STATE_START -->
{
  "run_id": 3,
  "schema_version": 6,
  "workflow": "default-with-output",
  "step": "plan",
  "agent": "architect",
  "phase": "planning",
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
        "step": "plan"
      }
    ]
  },
  "turn_outcome": {
    "status": "completed",
    "appreciation": "approved",
    "note": "Plan complete: guard the pre-existing openhands import in git_session.py per repo precedent instead of adding the SDK to the lint environment."
  },
  "prefs": {
    "complexity": "low"
  },
  "gate": {
    "step": "plan"
  },
  "resolution": {
    "task": "architect",
    "input_workflow": "default-with-output",
    "input_step": "plan",
    "input_complexity": "N/A",
    "role_name": "architect",
    "prompt_location": "/home/runner/work/nebuko/nebuko/defaults/prompts/architect.md",
    "agent_selected": "true",
    "selected_workflow": "default-with-output",
    "selected_step": "plan",
    "reasoning": "none",
    "matched_rule": "workflow:default-with-output/step:plan",
    "timestamp": "2026-10-23T23:18:31.699038+00:00"
  },
  "workflow_ref": "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@refs/heads/main",
  "position": [
    {
      "workflow": "default-with-output",
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

# Guard `get_logger` import in `git_session.py` — fix lint job without installing the SDK

## Decision
<!-- nebuko:section decision -->
## Context (root cause)

`python -m nebuko.engine lint-config` (lint job `internal-lint-agent-config.yaml`, base deps only) imports `nebuko/engine/__init__.py` → `run_loop.py:30` → `git_session.py:33`, which does an unconditional `from openhands.sdk import get_logger`. `openhands-sdk` exists only in the optional `runtime` extra (deliberately — the library must import without the SDK), so the job can never succeed as-is. The import predates PR #1106 (introduced by #1090); #1106 merely touched lint-trigger paths.

## Decision

Do **not** add `openhands` to the default environment. Guard the import in `src/nebuko/engine/git_session.py` with the repo's established fallback pattern (verbatim precedent: `load_price_table.py:20-26`, issue #928):

```python
try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    get_logger = logging.getLogger
```

Key rulings:
- Keep `openhands-sdk` optional: installing a git-pinned SDK in a fast PR lint job is slow, network-fragile, and contradicts the documented design intent (`pyproject.toml:19-20` — the library imports without the SDK).
- Only `git_session.py` needs the guard: it is the sole unconditional `openhands` import on the lint-job import chain; the other openhands import sites are either already guarded (`load_price_table.py`) or function-local / inside `lib/agent_script` features not on the chain.
- After the fix, verify the whole import chain with base deps — the traceback stops at the first missing module, so further unguarded imports later in `run_loop.py`'s import block would only surface after this change.

## Approaches considered

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| Guard import with `try/except ImportError` fallback | Repo precedent (#928); minimal diff; lint stays fast and offline | Slightly weaker logger outside the runtime extra (acceptable) | ✅ |
| Install `openhands-sdk` in the lint job / base deps | No code change | Git dependency in PR lint; slow, fragile, contradicts optional-runtime design | ❌ |
| Lazy/function-local logger in `git_session.py` | Avoids module-level guard | More churn; diverges from established pattern | ❌ |
<!-- /nebuko:section -->

## Design
<details><summary>Design</summary>

<!-- nebuko:section design -->
## Object Design Contract

No new or changed classes — the change is a module-level import guard plus `import logging`, mirroring `load_price_table.py:20-26`; `GitSessionGuard`/`GuardResult` signatures are untouched.

## Implementation Plan

<details>
<summary>Implementation Plan</summary>

1. **`src/nebuko/engine/git_session.py`** — add `import logging` (stdlib block, ~line 29) and wrap the line-33 `from openhands.sdk import get_logger` in the `try/except ImportError: get_logger = logging.getLogger` guard, same comment style as `load_price_table.py:20-26` (reference the lint job). Why: makes the module importable with base deps only.
2. **Verification (dev)** — in a clean env with only `pip install pyyaml pydantic`, run `PYTHONPATH=src python -m nebuko.engine lint-config defaults/agents/`; if a second `ModuleNotFoundError` surfaces further down `run_loop.py`'s import chain, apply the same guard to that import site and re-run until the lint command completes.
3. **Regression** — run `tests/engine/test_git_session.py` and the broader engine test suite with the runtime extra installed to confirm logging behavior is unchanged.
</details>
<!-- /nebuko:section -->
</details>

## Questions
<!-- nebuko:section questions -->
- Assumption: the fix lands as its own change off `main`, not inside PR #1106 — the broken import is pre-existing (#1090) and #1106 only changed constants. Default: separate fix; #1106's lint passes once its branch picks up the fix.
- Assumption: lint jobs should stay minimal-install (no `openhands`), per `pyproject.toml`'s documented design intent. Default: proceed with the guard.
<!-- /nebuko:section -->