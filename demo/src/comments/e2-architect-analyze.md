<!-- bot-workflow-terminal --><!-- workflow-continue -->
<!-- bot-agent-architect -->
<details>
<summary><picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> Workflow State (run 1)</summary>

```json
<!-- WORKFLOW_STATE_START -->
{
  "run_id": 1,
  "schema_version": 6,
  "workflow": "default-with-output",
  "step": "analyze",
  "agent": "architect",
  "phase": "analysis",
  "history": [
    {
      "step": "analyze",
      "agent": "architect",
      "summary": "Context request posted and hand-off to @@​context recorded.",
      "position": [
        {
          "workflow": "default-with-output",
          "step": "analyze"
        }
      ],
      "turn_outcome": {
        "status": "completed",
        "appreciation": "approved",
        "note": "Analysis step report: scoped the CI dependency failure and handed fact collection to @@​context."
      },
      "prefs": {
        "complexity": "low"
      },
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
  "turn_outcome": {
    "status": "completed",
    "appreciation": "approved",
    "note": "Analysis step report: scoped the CI dependency failure and handed fact collection to @@​context."
  },
  "prefs": {
    "complexity": "low"
  },
  "resolution": {
    "task": "architect",
    "input_workflow": "default-with-output",
    "input_step": "analyze",
    "input_complexity": "N/A",
    "role_name": "architect",
    "prompt_location": "/home/runner/work/nebuko/nebuko/defaults/prompts/architect.md",
    "agent_selected": "true",
    "selected_workflow": "default-with-output",
    "selected_step": "analyze",
    "next_step": "context",
    "step_skills": "planning",
    "reasoning": "none",
    "matched_rule": "workflow:default-with-output/step:analyze",
    "timestamp": "2026-10-23T23:15:09.581151+00:00"
  },
  "function_call": {
    "agent_name": "context",
    "task": "Collect facts about the failing lint CI job on PR #1106 (ModuleNotFoundError: No module named 'openhands' when running `python -m nebuko.engine lint-config defaults/agents/`):\n\n1. CI workflow definition: find the GitHub Actions workflow in `.github/workflows/` that runs `python -m nebuko.engine lint-config defaults/agents/`. Report verbatim its checkout + setup steps: python version, all pip install commands (e.g. `pip install -e .`, extras like `.[dev]`, any explicit `openhands` install), and working directory.\n2. Dependency declarations: from `pyproject.toml` (or `setup.py`/`setup.cfg`), report the `dependencies` list, all optional-dependencies/dev groups, and whether `openhands` / `openhands-sdk` appears anywhere (dependencies, extras, requirements*.txt, uv.lock, poetry.lock).\n3. The failing import: report `src/nebuko/engine/git_session.py` lines 1-60 (import block around line 33 `from openhands.sdk import get_logger`), and grep the whole repo for `openhands` imports \u2014 list every file + line number that imports it (src/, tests/, scripts/).\n4. Existing optional-dependency pattern: grep for `ImportError`, `TYPE_CHECKING`, `importlib` in `src/nebuko/` \u2014 is there an established lazy/optional import pattern to follow if `openhands` is meant to be optional?\n5. PR #1106 diff: which files it changes, especially anything touching `git_session.py`, imports, or CI config. Note whether the `openhands` import is new in this PR or pre-existing.\n6. Other CI jobs: how the test job(s) install dependencies \u2014 do they install `openhands` successfully (do tests pass on #1106)?",
    "complexity": "low"
  },
  "workflow_ref": "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@refs/heads/main",
  "position": [
    {
      "workflow": "default-with-output",
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

# CI lint job fails: `ModuleNotFoundError: No module named 'openhands'` in `nebuko.engine`

## Context_request
<details><summary>Context_request</summary>

<!-- nebuko:section context_request -->
Collect the following facts about the failing lint CI job on PR #1106:

1. **CI workflow definition**: find the GitHub Actions workflow that runs `python -m nebuko.engine lint-config defaults/agents/` (search `.github/workflows/`). Report verbatim its checkout + setup steps: python version, all pip install commands (e.g. `pip install -e .`, extras like `.[dev]`, any explicit `openhands` install), and working directory.
2. **Dependency declarations**: from `pyproject.toml` (or `setup.py`/`setup.cfg`), report the `dependencies` list, all optional-dependencies/dev groups, and whether `openhands` / `openhands-sdk` appears anywhere (dependencies, extras, lock files, requirements*.txt, uv.lock, poetry.lock).
3. **The failing import**: report `src/nebuko/engine/git_session.py` lines 1–60 (the import block around line 33 `from openhands.sdk import get_logger`), and grep the whole repo for `openhands` imports — list every file + line number that imports it (src/, tests/, scripts/).
4. **Existing optional-dependency pattern**: grep for `ImportError`, `TYPE_CHECKING`, `importlib` in `src/nebuko/` — is there an established lazy/optional import pattern we should follow if `openhands` is meant to be optional?
5. **PR #1106 diff**: which files it changes, especially anything touching `git_session.py`, imports, or CI config. Note whether the `openhands` import is new in this PR or pre-existing.
6. **Other CI jobs**: how the test job(s) install dependencies — do they install `openhands` successfully (i.e. do tests pass on #1106)? This tells us whether the dependency is resolvable at all in this repo's environment.
<!-- /nebuko:section -->
</details>

## Assumptions
<!-- nebuko:section assumptions -->
- The lint job installs the package without the dependency group that provides `openhands` (likely `openhands-sdk`), while the import in `git_session.py` is unconditional — fix will be either adding the dep to the lint job's install step, declaring it properly in `pyproject.toml`, or making the import lazy.
- If `openhands` is only used for `get_logger`, a stdlib `logging` fallback or lazy import may be the cleaner fix; I'll decide once I see how widely it's imported.
- Complexity treated as **low**: narrow CI/dependency fix, single import site plus one workflow file.
<!-- /nebuko:section -->