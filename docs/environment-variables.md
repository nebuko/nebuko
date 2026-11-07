---
title: "Environment variables"
audience: operator
covers:
  - src/nebuko/engine/lib/event_normalizer.py
  - src/nebuko/engine/lib/resolve_config.py
sidebar_group: "Reference"
sidebar_label: "Environment variables"
sidebar_order: 15
---

# Environment variables

Variables the orchestrator sets/consumes per run:

| Variable | Set by | Description |
|----------|--------|-------------|
| `TARGET_NUMBER` | `event_normalizer.py` | Issue/PR number the run targets. |
| `TRIGGER_BODY` | `event_normalizer.py` | Raw text of the triggering issue body or comment. |
| `TRIGGER_AUTHOR` | `event_normalizer.py` | Author of the triggering event. |
| `EVENT_TYPE` | `event_normalizer.py` | GitHub event type (`issues`, `issue_comment`, `workflow_call`, ...). |
| `ROLE_NAME` | `resolve_config` / `resolve_workflow` | Resolved agent role (`architect`, `context`, `dev`, `review`, ...). |
| `PROMPT_LOCATION` | `resolve_config` / `resolve_workflow` | Path to the resolved prompt file. |
| `WORKFLOW_NAME` | `resolve_config` / `resolve_workflow` | Resolved workflow name. |
| `WORKFLOW_STEP` | `resolve_config` / `resolve_workflow` | Resolved current step name. |
| `WORKFLOW_NEXT` | `resolve_config` / `resolve_workflow` | Next step name after the current one. |
| `STEP_PROMPT_FILE` | `resolve_config` / `resolve_workflow` | Path to the step's standing instruction file (`steps[].prompt`). Empty when the step has no prompt. |
| `CONFIG_COMPLEXITY` | `resolve_config` | Effective tier for this run (`low`/`medium`/`high`): an explicit dispatch `complexity`, else the step's `steps[].complexity`, else `medium`. Drives `agents.<name>.complexity_overrides.<tier>` and the tier temperature floor (issue #787). |
| `RUN_SCRIPT` | `resolve_config` | `"true"` when the resolved step is a deterministic `script` step (no agent runs). |
| `SCRIPT_PATH` | `resolve_config` | The step's `script.command` verbatim (executable or repo-relative script). |
| `SCRIPT_ARGS` | `resolve_config` | JSON array of the step's `script.args` argv entries (empty array when none); decoded with `json.loads` at spawn. |
| `SCRIPT_WORKING_DIR` | `resolve_config` | Repo-relative working directory for the script (from `script.working_dir`; empty → checkout root). |
| `SCRIPT_ENV` | `resolve_config` | JSON object of extra environment variables (from `script.env`; empty object when none), overlaid on the engine environment. |
| `SCRIPT_TIMEOUT_MINUTES` | `resolve_config` | Script timeout in minutes (empty → default 30). |
| `PARENT_WORKFLOW` | `resolve_config` / `resolve_workflow` | Parent workflow name when the run is nested in a sub-workflow mount; empty at top level. Derived from the second-to-last `position` frame. |
| `PARENT_WORKFLOW_STEP` | `resolve_config` | Step in the parent workflow that mounted a sub-workflow. |
| `WORKFLOW_POSITION` | `resolve_config` / `resolve_workflow` | Single-line JSON of the run's full mount stack (`WorkflowState.position`). |
| `WORKFLOW_POSITION_PATH` | `resolve_config` / `resolve_workflow` | Human-readable `wf/step\|wf/step\|...` rendering of `WORKFLOW_POSITION`. |
| `STEP_INPUTS` | `resolve_config` | Single-line JSON of a resolved step's typed-input wiring (issue #813): the consumer step's own bindings plus the call-frame chain (each frame's callee, call step and frozen bindings) derived from the carried-forward `WORKFLOW_POSITION`. Emitted only when the run actually wires something. Empty/absent otherwise. |
| `HISTORY_COMMENT_COUNT` | reusable workflow (`history_comment_count`) | Thread-context bound (ADR-0003, issues #844/#872). Unset or non-positive → the prompt's `## Recent thread` renders the **full relevant flow** classified from the thread ledger; a positive N caps it to the last N relevant records (pinned comments always added on top). The shipped reusable workflow defaults to `5`, so a CI run tail-caps the ledger unless the input is set to `0`. The loop guard's raw-history fallback still uses a positive default (5). |
| `DEBUG` | user (`DEBUG=true` in body) / flag | Enables verbose debug logging. |
| `OPENROUTER_API_KEY` | repo secret | LLM provider token (OpenRouter). |
| `LOCAL_VLLM_API_TOKEN` | repo secret (optional) | Local vLLM provider token. |
| `COMMANDCODE_API_KEY` | repo secret (optional) | CommandCode subscription token — see [subscription-plans.md](subscription-plans.md). |
| `GH_PAT` | repo secret (PAT fallback only) | Fine-grained GitHub PAT used for git push and API calls when the Nebuko App is not installed. App-mode runs mint a short-lived installation token from the OIDC broker instead. |

`event_normalizer.py` emits `KEY=value` pairs (multi-line values use HEREDOC
form `KEY<<EOF ... EOF`) for `eval $(...)` consumption.
