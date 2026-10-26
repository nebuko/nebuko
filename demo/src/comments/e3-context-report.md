<!-- bot-workflow-terminal --><!-- workflow-continue -->
<!-- bot-agent-context -->
<details>
<summary><picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> Workflow State (run 2)</summary>

```json
<!-- WORKFLOW_STATE_START -->
{
  "run_id": 2,
  "schema_version": 6,
  "workflow": "default-with-output",
  "step": "context",
  "agent": "context",
  "phase": "analysis",
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
        "step": "context"
      }
    ]
  },
  "turn_outcome": {
    "status": "completed",
    "appreciation": "approved",
    "note": "All six requested fact groups collected verbatim: lint workflow install steps, pyproject dependency/extra declarations, the failing import chain, existing ImportError fallback patterns, PR #1106 diff scope, and the absence of any other PR-check job installing openhands."
  },
  "resolution": {
    "task": "context",
    "input_workflow": "default-with-output",
    "input_step": "context",
    "input_complexity": "low",
    "role_name": "context",
    "prompt_location": "/home/runner/work/nebuko/nebuko/defaults/prompts/context.md",
    "agent_selected": "true",
    "selected_workflow": "default-with-output",
    "selected_step": "context",
    "next_step": "plan",
    "reasoning": "none",
    "matched_rule": "workflow:default-with-output/step:context",
    "timestamp": "2026-10-23T23:17:26.993708+00:00"
  },
  "callback": "architect",
  "workflow_ref": "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@refs/heads/main",
  "position": [
    {
      "workflow": "default-with-output",
      "step": "context"
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

*Context answer*

## 🔍 Context Collection Report — CI lint job `ModuleNotFoundError: No module named 'openhands'` (PR #1106)

Executive summary: The failing job is `.github/workflows/internal-lint-agent-config.yaml`, which installs only `pyyaml pydantic` and then runs `python -m nebuko.engine lint-config defaults/agents/` with `PYTHONPATH: src`. That command imports `nebuko.engine.__init__` → `run_loop` → `git_session`, and `git_session.py:33` does an unconditional `from openhands.sdk import get_logger`. `openhands-sdk` is declared only in the optional `[project.optional-dependencies] runtime` extra of `pyproject.toml` (installed solely by the caller workflow `nebuko-workflow.yaml` via `uv run --extra runtime`), so the lint job can never resolve it. The import is pre-existing on main (introduced by merged PR #1090, "Fix #1089: fail closed on git credentials…"); PR #1106 only edits constants in `git_session.py` but touches `config_lint.py`/`run_loop.py`, which are lint-job trigger paths. The repo has an established fallback pattern for exactly this situation: `try: from openhands.sdk import get_logger / except ImportError: get_logger = logging.getLogger` in `load_price_table.py:20-26` (added for the summarize-metrics job, issue #928). PR #1106's only CI check is `lint` (FAILURE); no test job runs on PRs.

<details>
<summary>FILES FOUND — Files located for the six requested fact groups.</summary>

### FILES FOUND

*Files located for the six requested fact groups.*

- `.github/workflows/internal-lint-agent-config.yaml` (whole file, 27 lines) — the failing workflow; installs only `pyyaml pydantic`, runs `python -m nebuko.engine lint-config defaults/agents/` with `PYTHONPATH: src`, Python 3.13.
- `pyproject.toml:12-33` — `dependencies` = pyyaml/pydantic/rich only; `openhands-sdk`/`openhands-tools` appear solely in the optional `runtime` extra, pinned to a software-agent-sdk commit SHA.
- `src/nebuko/engine/git_session.py:30-35` — the failing import site: unconditional `from openhands.sdk import get_logger` at line 33, `logger = get_logger(__name__)` at line 35.
- `src/nebuko/engine/run_loop.py:30` — `from nebuko.engine.git_session import GitSessionGuard, GuardResult` (the chain link in the traceback).
- `src/nebuko/engine/__init__.py:9` — `from nebuko.engine.run_loop import PHASES, Engine, main` (entry point of `python -m nebuko.engine`).
- `src/nebuko/engine/lib/pricing/features/load_price_table/load_price_table.py:20-26` — existing `try/except ImportError` fallback for `get_logger` (issue #928 precedent).
- `src/nebuko/engine/lib/resolve_workflow.py:51-54` — second existing `try/except ImportError` pattern (optional `remote_workflow`).
- `.github/workflows/nebuko-workflow.yaml:303-308, 330-333` — the only place openhands is installed: `uv pip install --system "openhands-sdk @ git+…@main#subdirectory=openhands-sdk"` and runs use `uv run --project "$NEBUKO_ROOT" --extra runtime`.
- `.github/workflows/internal-lint-workflows.yaml` (whole file) — sibling lint job with the same minimal `pip install pyyaml pydantic` install (runs `python -m engine lint workflows/`, does not import nebuko.engine).
- `uv.lock:2439-2440, 2452-2453, 2520-2547` — openhands-sdk/tools locked, marker `extra == 'runtime'`.
- PR #1106 diff, `src/nebuko/engine/git_session.py` hunk — changes only `GIT_IDENTITY_NAME`/`GIT_IDENTITY_EMAIL` constants and a docstring; the openhands import is untouched by this PR.

</details>

<details>
<summary>RELEVANT SNIPPETS — Verbatim code for every fact above.</summary>

### RELEVANT SNIPPETS

*Verbatim code for every fact above.*

The failing workflow (full file):

```yaml
# .github/workflows/internal-lint-agent-config.yaml
 1  name: Lint agent config
 2
 3  on:
 4    pull_request:
 5      paths:
 6        - "defaults/agents/**"
 7        - "src/nebuko/engine/config_lint.py"
 8        - "src/nebuko/engine/config_lint_cli.py"
 9        - ".github/workflows/lint-agent-config.yaml"
10
11  permissions:
12    contents: read
13
14  jobs:
15    lint:
16      runs-on: ubuntu-latest
17      steps:
18        - uses: actions/checkout@v5
19        - uses: actions/setup-python@v6
20          with:
21            python-version: "3.13"
22        - name: Install dependencies
23          run: pip install pyyaml pydantic
24        - name: Lint agent-config YAML
25          env:
26            PYTHONPATH: src
27          run: python -m nebuko.engine lint-config defaults/agents/
```

Dependency declarations (`pyproject.toml:12-33`; no `openhands` outside the `runtime` extra; no requirements*.txt / setup.py / setup.cfg exist):

```toml
12  dependencies = [
13      "pyyaml>=6",
14      "pydantic>=2",
15      "rich>=13",
16  ]
17
18  [project.optional-dependencies]
19  # The agent runtime is installed by CI / the dev shell; kept optional so the
20  # library (engine + adapters) can be imported without pulling the SDK.
21  # Pinned to a commit SHA, not `main` (issue #982): upstream moves several
22  # commits a day, and an unpinned install makes any run's behavior
23  # unreproducible. Bump the SHA in one place — here, the CI install step in
24  # `.github/workflows/nebuko-workflow.yaml`, and `uv.lock` (re-resolve with
25  # `uv lock`).
26  runtime = [
27      "litellm",
28      "openhands-sdk @ git+https://github.com/OpenHands/software-agent-sdk.git@8ba966d1d3af49f08e04349c055009e1614b48d6#subdirectory=openhands-sdk",
29      "openhands-tools @ git+https://github.com/OpenHands/software-agent-sdk.git@8ba966d1d3af49f08e04349c055009e1614b48d6#subdirectory=openhands-tools",
30  ]
31
32  [project.scripts]
33  nebuko = "nebuko.engine.run_loop:main"
```

The failing import (`src/nebuko/engine/git_session.py:26-35`; module docstring is lines 1-24, all about the git session guard):

```python
26  from __future__ import annotations
27
28  import base64
29  import subprocess
30  from collections.abc import Mapping
31  from dataclasses import dataclass
32  from pathlib import Path
33  from openhands.sdk import get_logger
34
35  logger = get_logger(__name__)
```

The import chain (traceback path):

```python
# src/nebuko/engine/__init__.py:9
from nebuko.engine.run_loop import PHASES, Engine, main

# src/nebuko/engine/run_loop.py:30
from nebuko.engine.git_session import GitSessionGuard, GuardResult

# src/nebuko/engine/git_session.py:33
from openhands.sdk import get_logger
```

Every `openhands` import in src/ and tests/ (grep, file:line):

```text
src/nebuko/engine/git_session.py:33: from openhands.sdk import get_logger
src/nebuko/engine/lib/pricing/features/load_price_table/load_price_table.py:21: from openhands.sdk import get_logger   (guarded, see below)
src/nebuko/engine/lib/resolve_config/features/skill_context/skill_context.py:100: from openhands.sdk.skills.skill import Skill   (function-local)
src/nebuko/engine/lib/agent_script/features/finalize_tool/finalize_tool.py:19: from openhands.sdk.tool.tool import (…)
src/nebuko/engine/lib/agent_script/features/finalize_tool/finalize_tool.py:48-49: TYPE_CHECKING imports (BaseConversation, ConversationState)
src/nebuko/engine/lib/agent_script/features/call_agent_tool/call_agent_tool.py:19-20,33-34: get_logger, tool imports, TYPE_CHECKING imports
src/nebuko/engine/lib/agent_script/features/extract_recovery_trace/extract_recovery_trace.py:13-15: get_logger, ActionEvent, ObservationEvent
src/nebuko/engine/lib/agent_script/features/extract_recovery_trace/test_extract_recovery_trace.py:93,97,110: function-local test imports
src/nebuko/engine/lib/agent_script/features/build_output/build_output.py:10-14: get_logger, get_agent_final_response, ActionEvent/MessageEvent, content_to_str, FinishTool
src/nebuko/engine/lib/agent_script/features/build_output/test_build_output.py:1118-1162; test_build_output_agent_checkpoint.py:27-28; test_build_output_failure.py:11-12; test_build_output_graceful_timeout.py:14-15: test imports
src/nebuko/engine/lib/agent_script/features/run_agent/run_agent.py:15-18: from openhands.sdk import Agent, Conversation, LLM, get_logger; context.AgentContext; conversation.get_agent_final_response; mcp.MCPServer
```

Established optional-import pattern (the repo's precedent for a job that installs base deps only):

```python
# src/nebuko/engine/lib/pricing/features/load_price_table/load_price_table.py:20-26
try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    # The summarize metrics job installs base deps (no openhands) yet must
    # still import this module to price zero-cost entries (issue #928).
    get_logger = logging.getLogger

logger = get_logger(__name__)
```

```python
# src/nebuko/engine/lib/resolve_workflow.py:51-54
try:
    from remote_workflow import fetch_remote_workflow
except ImportError:
    fetch_remote_workflow = None  # type: ignore
```

Where openhands IS installed successfully (caller workflow, not a PR check):

```yaml
# .github/workflows/nebuko-workflow.yaml:303-308
            - name: Install OpenHands dependencies
              run: |
                  ENCODED=$(echo -n "x-access-token:${GH_TOKEN}" | base64 -w 0)
                  git config --global http.https://github.com/.extraheader "AUTHORIZATION: basic $ENCODED"
                  uv pip install --system "openhands-sdk @ git+https://github.com/OpenHands/software-agent-sdk.git@main#subdirectory=openhands-sdk"
                  uv pip install --system "openhands-tools @ git+https://github.com/OpenHands/software-agent-sdk.git@main#subdirectory=openhands-tools"

# .github/workflows/nebuko-workflow.yaml:330-333
              run: uv run --project "$NEBUKO_ROOT" --extra runtime python -m nebuko.engine.lib.secret_env
            - name: Run orchestration engine
              run: uv run --project "$NEBUKO_ROOT" --extra runtime python -m nebuko.engine.run
```

Sibling lint job (same minimal install; does not import `nebuko.engine`, so it does not hit the missing module):

```yaml
# .github/workflows/internal-lint-workflows.yaml (steps 17-24)
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"
      - name: Install dependencies
        run: pip install pyyaml pydantic
      - name: Lint workflow YAML
        run: python -m engine lint workflows/
```

PR #1106 diff on `git_session.py` (the import is untouched — only constants change):

```diff
--- a/src/nebuko/engine/git_session.py
+++ b/src/nebuko/engine/git_session.py
@@ -35,9 +35,9 @@
 logger = get_logger(__name__)

 # @stub-protected
-GIT_IDENTITY_NAME: str = "nebuko-agent"
+GIT_IDENTITY_NAME: str = "nebuko"
 # @stub-protected
-GIT_IDENTITY_EMAIL: str = "nebuko-agent@users.noreply.github.com"
+GIT_IDENTITY_EMAIL: str = "nebuko@nebuko.dev"
```

PR #1106 metadata (via `gh pr view 1106`): title "feat: fetch_comment_bodies tool for agent context recall (#748)", branch `feature/issue-748-fetch-comment-bodies`; files: `docs/adoption.md`, `src/nebuko/engine/answer_capture.py`, `config_lint.py`, `engine_tools.py`, `git_session.py`, `run_loop.py`, `lib/agent_script/features/fetch_comment_bodies_tool/{__init__,fetch_comment_bodies_tool}.py` (new), `lib/agent_script/features/run_agent/run_agent.py`, `lib/agent_script/sub_domains/tool_resolution/{test_tool_resolution,tool_resolution}.py`, `tests/engine/test_config_lint.py`, `test_fetch_comment_bodies_tool.py`, `test_git_session.py`, `test_run_loop.py`. Check rollup: single check `lint` → FAILURE. The new `fetch_comment_bodies_tool.py` contains no `openhands` imports.

</details>

<details>
<summary>EXTERNAL REFERENCES — Repo-internal CI facts; GitHub PRs are the primary references (fetched via `gh` on 2026-10-23).</summary>

### EXTERNAL REFERENCES

*Repo-internal CI facts; GitHub PRs are the primary references (fetched via `gh` on 2026-10-23).*

- **PR #1106 — feat: fetch_comment_bodies tool for agent context recall (#748)**
  **URL:** https://github.com/nebuko/nebuko/pull/1106
  **Summary:** The PR under lint; touches `config_lint.py` and `run_loop.py` (lint trigger paths) and edits constants in `git_session.py`. Its only CI check `lint` fails with the reported ModuleNotFoundError; the openhands import itself is pre-existing.
  **Score:** 1.0
- **PR #1090 — Fix #1089: fail closed on git credentials and on unpublished session work**
  **URL:** https://github.com/nebuko/nebuko/pull/1090
  **Summary:** The merged PR that introduced `src/nebuko/engine/git_session.py` (with the unconditional `from openhands.sdk import get_logger`) and wired it into `run_loop.py` — i.e. the failing import chain predates #1106.
  **Score:** 0.9
- **Issue #928 precedent — summarize metrics job installs base deps (no openhands)**
  **URL:** (referenced verbatim in `load_price_table.py:23`)
  **Summary:** In-repo precedent: a CI job that installs only base deps must still import a module using `get_logger`; solved there with `try/except ImportError: get_logger = logging.getLogger`.
  **Score:** 0.8
- **OpenHands software-agent-sdk (source of `openhands-sdk` / `openhands-tools`)**
  **URL:** https://github.com/OpenHands/software-agent-sdk
  **Summary:** Git dependency pinned at SHA `8ba966d1d3af49f08e04349c055009e1614b48d6` in `pyproject.toml` `runtime` extra; provides the `openhands.sdk.get_logger` symbol the lint job cannot resolve.
  **Score:** 0.5

```json
[
  {
    "url": "https://github.com/nebuko/nebuko/pull/1106",
    "date": "2026-10-23T23:15:00Z",
    "chunks": [
      "title: feat: fetch_comment_bodies tool for agent context recall (#748)",
      "checks: [{\"conclusion\": \"FAILURE\", \"name\": \"lint\"}]",
      "diff --git a/src/nebuko/engine/git_session.py b/src/nebuko/engine/git_session.py\n-GIT_IDENTITY_NAME: str = \"nebuko-agent\"\n+GIT_IDENTITY_NAME: str = \"nebuko\"\n-GIT_IDENTITY_EMAIL: str = \"nebuko-agent@users.noreply.github.com\"\n+GIT_IDENTITY_EMAIL: str = \"nebuko@nebuko.dev\""
    ],
    "summary": "PR under lint; touches lint trigger paths (config_lint.py, run_loop.py) and only constants in git_session.py; single failing check is the lint job.",
    "score": 1.0
  },
  {
    "url": "https://github.com/nebuko/nebuko/pull/1090",
    "date": "2026-10-23T23:15:00Z",
    "chunks": [
      "Fix #1089: fail closed on git credentials and on unpublished session work",
      "files: [\"src/nebuko/engine/git_session.py\", \"src/nebuko/engine/run_loop.py\", \"tests/engine/test_git_session.py\", \"…\"]"
    ],
    "summary": "Merged PR that introduced git_session.py with the unconditional openhands.sdk import and its run_loop.py wiring — the failing chain predates PR #1106.",
    "score": 0.9
  },
  {
    "url": "https://github.com/OpenHands/software-agent-sdk",
    "date": "2026-10-23T23:15:00Z",
    "chunks": [
      "openhands-sdk @ git+https://github.com/OpenHands/software-agent-sdk.git@8ba966d1d3af49f08e04349c055009e1614b48d6#subdirectory=openhands-sdk"
    ],
    "summary": "Upstream SDK providing openhands.sdk.get_logger; declared only in the optional runtime extra, pinned to a commit SHA (issue #982).",
    "score": 0.5
  }
]
```

</details>

<details>
<summary>OTHER FILES — Examined and found not directly relevant to the failure.</summary>

### OTHER FILES

*Examined and found not directly relevant to the failure.*

- `.github/workflows/internal-publish-docs.yaml`, `internal-release.yml`, `interntal-trigger-nebuko-on-comment.yaml` — no `openhands` or `pip install` of the engine; not part of the failing path.
- `src/nebuko/engine/config_lint.py`, `config_lint_cli.py` — no `openhands` imports; `config_lint_cli.py` imports only `yaml`, `nebuko.engine.config_lint`, `nebuko.engine.lint_cli` (the failure happens earlier, at package `__init__`).
- `src/nebuko/engine/answer_capture.py:219`, `run_loop.py:1494` — mention `openhands` only in docstrings/comments, no imports.
- `tests/engine/test_git_session.py` — no `openhands` imports; imports `nebuko.engine.git_session` (would also fail without the SDK, but no test job runs on PRs).
- `uv.lock` — confirms openhands-sdk/tools are locked with marker `extra == 'runtime'` only.
- Repo root contains stray artifacts (`issue-1114-1.md`, a `pi-session-*.html` console dump, `openhands-console.txt`) — unrelated to this failure.
</details>