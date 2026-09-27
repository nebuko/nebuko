# Default Workflow — `openhands-multi-agent.yml`

> **Purpose:** Technical reference for AI agents to understand the current behavior before refactoring.  
> **File:** `.github/workflows/openhands-multi-agent.yml` (~964 lines)  
> **Role:** Central orchestrator — routes GitHub events to the appropriate AI agent based on mention patterns and configuration.

---

## 1. Overview

This workflow acts as a **multi-agent router**. It listens to GitHub events (`issues`, `issue_comment`, `workflow_call`), determines which agent should handle the request, resolves LLM/model configuration, runs the OpenHands agent, and posts the result back as a comment.

### Trigger Mechanisms

| Trigger | Condition |
|---------|-----------|
| `issues` | Issue opened, edited, assigned, or closed - body must contain an agent mention (`@@genius`, `@@dev`, etc.) or `/workflow`. Bot-tagged bodies (starting with `<!-- bot-agent-` or `<!-- bot-workflow-terminal -->`) never fire on `@@mentions` |
| `issue_comment` | New comment - body must contain an agent mention, or a machine trigger token: `"function_call"` (in the posted state JSON), `<!-- workflow-continue -->`, `<!-- bot-summarizer-trigger -->`, `<!-- bot-subtask-complete: N -->`, `<!-- bot-trigger: agent -->`. `@@mention` firing is suppressed when the comment starts with a bot marker |
| `workflow_call` | Reusable workflow invoked by another workflow; uses `prompt_body` input |

### Agent Roles

| Role | Mention | Responsibility |
|------|---------|---------------|
| `genius` | `@@genius` | Principal engineer — plans, decomposes tasks |
| `ctx` | `@@ctx` | Context builder — gathers repository context |
| `dev` | `@@dev` | Execution developer — implements code changes |
| `review` | `@@review` | Code reviewer — reviews PRs |
| `issue` | `@@issue` | Issue processor |
| `issue-reviewer` | `@@issue-reviewer` | Issue reviewer |
| `debug` | `@@debug` | Debug specialist |
| `summarize` | (auto-triggered) | Summarizes long conversations |

---

## 2. Jobs

The workflow defines three jobs:

1. **`run-task`** — Main job that processes triggers and runs agents.
2. **`summarize-metrics`** — Runs when an issue is closed (not a PR), aggregating metrics across all agent runs on that issue.
3. **`subtask-completion-notifier`** — Runs when a sub-task issue (title starts with `[Sub-task`) is closed; notifies the parent issue.

---

## 3. Job: `run-task` — Step-by-Step

### Step 1: Checkout Repository

**Input:** Repository context from the GitHub event.  
**Output:** Local copy of the repository in `$GITHUB_WORKSPACE`.

Standard `actions/checkout@v5` to clone the target repository.

---

### Step 2: Check if git-flow-ai Checkout is Needed

**Input:** Filesystem presence of `.agents/prompts/genius.md`.  
**Output:** `needed` output (`true` or `false`).

Determines whether the repository already has git-flow-ai embedded. If the prompt file exists, the repo is self-hosting the tooling. If not, the git-flow-ai repo must be checked out separately (for reusable workflow mode).

---

### Step 3: Checkout git-flow-ai Repo (Reusable Workflow Mode)

**Input:** `needed` from Step 2.  
**Output:** git-flow-ai repository cloned at `.git-flow-ai-cache/` (conditional).

Only runs when `needed=true`. Clones `olivier-norture/git-flow-ai` into `.git-flow-ai-cache/` using `GH_PAT`. Provides scripts and prompts for repositories that don't embed git-flow-ai directly. The "Resolve Agent Configuration" step exports `GIT_FLOW_AI_SCRIPTS` as the single source of truth for script locations (cache-aware), consumed by all subsequent script invocations.

---

### Step 4: Normalize GitHub Event Data

**Input:** `github.event_name`, `GITHUB_EVENT_PATH`, `inputs.prompt_body` (for workflow_call).  
**Output:** Environment variables: `TARGET_NUMBER`, `TRIGGER_BODY`, `TRIGGER_AUTHOR`, `EVENT_TYPE`, `ISSUE_URL`, `REPO_NAME`.

Normalizes the three possible event types into a uniform set of variables. This abstraction allows downstream steps to operate without branching on event type.

| Event Type | `TARGET_NUMBER` | `TRIGGER_BODY` source |
|------------|-----------------|----------------------|
| `issue_comment` | Issue number | `.comment.body` |
| `issues` | Issue number | `.issue.body` |
| `workflow_call` | `0` | `inputs.prompt_body` |

---

### Step 5: Set Up Python

**Input:** `PYTHON_VERSION` env var (default `3.13`).  
**Output:** Python runtime available.

Installs Python via `actions/setup-python@v6`.

---

### Step 6: Install uv

**Input:** `ENABLE_UV_CACHE` env var.  
**Output:** `uv` package manager installed.

Installs Astral's `uv` via `astral-sh/setup-uv@v7`.

---

### Step 7: Install OpenHands Dependencies

**Input:** None.  
**Output:** `openhands-sdk` and `openhands-tools` Python packages installed.

Installs the two core OpenHands packages directly from the `agent-sdk` GitHub repository's `main` branch.

---

### Step 8: Resolve Agent Configuration

**Input:** `TRIGGER_BODY`, `PROMPT_BASE`, `WFLOWS_DIR`.  
**Output:** Environment variables: `TASK`, `ROLE_NAME`, `PROMPT_LOCATION`, `AGENT_SELECTED`, `COMPLEXITY`, `LLM_REASONING`, `AGENT_ROUND`, `AGENT_SKILLS`, `STEP_SKILLS`, `CONFIG_MODEL`, `CONFIG_TEMPERATURE`, `CONFIG_TOP_P`, `CONFIG_TOP_K`, `CONFIG_REPETITION_PENALTY`, `WORKFLOW_NAME`, `WORKFLOW_STEP`, `WORKFLOW_NEXT`, `PARENT_WORKFLOW`, `PARENT_WORKFLOW_STEP`, `SKILL_CONTEXT_FILE`, `CONFIG_PR_NUMBER`.

Runs `resolve_config.py` which parses the trigger body to determine:
1. Which agent role should handle the request (from a human `@@mention`, a machine trigger token, the `AGENT_RUN_CONFIG` `function_call` section, or the workflow state's dispatch).
2. The complexity level (`low`, `medium`, `high`).
3. The prompt file location for the selected agent.
4. Skill files to load and concatenate.
5. Model/temperature overrides.
6. Workflow routing metadata (for multi-step workflows).

Routing priority: human `@@mention` > `AGENT_RUN_CONFIG` `function_call` (imperative one-shot hand-off) > machine trigger token > workflow-state dispatch > workflow next-step > bot fallback. Bot bodies never yield an `@@mention` (logged warning when mentions are dropped).

If resolution fails or the trigger should be skipped, sets `SKIP_AGENT=true` and exits.

---

### Step 9: Prevent Agent-Trigger Loops & Human Ping Guard

**Input:** `TRIGGER_BODY`, `TASK` (target agent).  
**Output:** `SKIP_AGENT=true` or `SKIP_AGENT=false`.

Two guards prevent unwanted re-execution:

**Guard 1 — Same-agent loop:** If the trigger body contains `<!-- bot-agent-X -->` and the target agent is also `X`, skip (same agent calling itself). Cross-agent calls are allowed.

**Guard 2 — Human ping guard:** If both an agent mention AND a human `@mention` appear in the same message, skip (let the human orchestrate).

---

### Step 10: Check Circuit Breaker

**Input:** `TARGET_NUMBER`, `CIRCUIT_BREAKER_THRESHOLD` (default 25).  
**Output:** `CIRCUIT_BREAKER_TRIPPED=true/false`, `SKIP_AGENT=true` if tripped. A comment posted to the issue if tripped.

Counts historical `<!-- bot-agent- -->` markers across all comments on the issue. If the count divided by 2 exceeds the threshold, the circuit breaker trips: execution halts and a comment is posted notifying the issue author. Summary markers (`<!-- bot-agent-summary -->`) are excluded from the count.

---

### Step 11: Check Summarizer Gate

**Input:** `TARGET_NUMBER`, `SUMMARIZER_THRESHOLD` (default 100).  
**Output:** `SUMMARIZER_NEEDED=true/false`, `SKIP_AGENT=true` if summarizer needed.

Checks if the issue has exceeded the comment threshold AND no existing summary comment is present. If so, sets `SUMMARIZER_NEEDED=true` and `SKIP_AGENT=true` to short-circuit the normal agent flow and trigger a summarizer instead.

---

### Step 12: Post Summarizer Trigger Comment

**Input:** `SUMMARIZER_NEEDED` from Step 11.  
**Output:** A new comment on the issue containing `<!-- AGENT_RUN_CONFIG {"task": "summarize", "complexity": "low"} -->`.

Conditional on `SUMMARIZER_NEEDED=true`. Posts a comment tagged with `<!-- bot-agent-summary -->` that will itself trigger the workflow again with the summarizer agent.

---

### Step 13: Post Acknowledgment Comment

**Input:** `ROLE_NAME`, `TARGET_NUMBER`, `github.run_id`.  
**Output:** A comment on the issue confirming the agent has started, with a link to workflow logs. Saves `ACK_COMMENT_ID`.

Posts a temporary "🤖 **{role} agent started**" comment that will be minimized later once the real answer is posted. The comment is tagged with `<!-- bot-agent-{role} -->`.

---

### Step 14: Save Acknowledgment Comment Node ID

**Input:** `ACK_COMMENT_ID` from Step 13.  
**Output:** `ACK_COMMENT_NODE_ID` (GraphQL node ID).

Fetches the GraphQL node ID of the acknowledgment comment for later minimization via the GraphQL API.

---

### Step 15: Prepare Agent Query Context

**Input:** Issue number, event path.  
**Output:** `/tmp/ready_prompt.md` containing the full query context, exported to `AGENT_QUERY_PATH`.

Runs `query_context.py` which gathers:
- Issue title and body.
- Comment history (with agent mentions stripped or preserved as context).
- Labels, assignees, and linked PR references.
- Any previous workflow state from earlier agent runs.

Produces a consolidated markdown prompt file.

---

### Step 16: Configure git to Use GH_PAT

**Input:** `GH_PAT` secret.  
**Output:** Git remote configured with PAT authentication.

Updates the git remote URL to embed the PAT, allowing the agent to push branches and create PRs.

---

### Step 17: Run OpenHands Agent

**Input:** `PROMPT_LOCATION`, `AGENT_QUERY_PATH`, `ROLE_NAME`, `COMPLEXITY`, `CONFIG_MODEL`, `CONFIG_TEMPERATURE`, `AGENT_CONFIG_PATH`, `LLM_API_KEY`, `LLM_BASE_URL`, `PR_NUMBER` (optional for review agent).  
**Output:** Agent execution results written to stdout → `console.log`, structured final message to `final_message.txt`, optional `split_plan.json`, `workflow_state.json`, and optional `final_message_part_{N}.txt` files.

Builds the final prompt by concatenating:
1. The role-specific prompt template (`$PROMPT_LOCATION`).
2. For `dev` role: extracted directive from the latest `@@dev` mention.
3. The query context (`$AGENT_QUERY_PATH`).
4. For `review` role: PR diff (discovered via explicit PR number → API query → regex fallback).

Runs the agent via `stdbuf -oL -eL uv run python "$AGENT_SCRIPT_PATH" "$FINAL_PROMPT_FILE"` with output logged to `console.log`.

**Graceful Interruption on Timeout:**
The step `timeout-minutes: 30` remains as a hard backstop only. In-process, the agent runs under a *soft* time budget (`AGENT_SOFT_TIMEOUT_MINUTES`, set to `20` in the workflow; library fallback `110`). When the budget expires, the in-flight LLM call is interrupted (`conversation.interrupt()`), a bounded wind-down prompt is injected (budget: `WIND_DOWN_TIMEOUT_MINUTES`, set to `10` in the workflow; library fallback `8`) asking the agent to summarize completed work, the blocking point, and remaining work, then commit and open/update a PR. A deterministic fallback (`ensure_pr`) commits the working tree and opens/updates the PR if the agent did not. A gracefully timed-out run feeds the standard recovery-log pipeline, so the next run resumes from a fresh session via the saved PR instead of starting over. Both knobs are optional env overrides resolved in `run_agent/timeout_budget.py` (budget decision recorded in ADR-0002, issue #639).

**PR Discovery Priority (review agent only):**
1. Explicit `CONFIG_PR_NUMBER` from the `AGENT_RUN_CONFIG` sentinel JSON.
2. GitHub API query for open PRs referencing the issue.
3. Regex scan of comment history for `PR #<digits>` pattern.

---

### Step 18: Clean and Prepare Comment

**Input:** `final_message.txt` (preferred), `console.log` (fallback), `TRIGGER_AUTHOR`.  
**Output:** `github_comment.md` containing the cleaned agent response. Sets `HAS_COMMENT=true`.

Four-path logic:
- **Path A:** `final_message.txt` exists and has content → use it (truncated to 60K chars).
- **Path B:** `final_message.txt` has sentinel `__NO_FINAL_MESSAGE__` or is empty → parse `console.log` for "Message from Agent" blocks, fall back to last 50 meaningful lines.
- **Path C:** No `console.log` either → crash message + preserved metrics.
- **Path D:** `final_message.txt` doesn't exist → fallback error message.

Metrics comments (`<!-- agent-metrics:... -->`) are preserved and appended to the output.

---

### Step 19: Add Bot Marker and Workflow State to Comment

**Input:** `github_comment.md`, `ROLE_NAME`, `workflow_state.json` (optional).  
**Output:** Updated `github_comment.md` with bot marker, display name prefix, and collapsed workflow state section.

Prepends to the comment:
1. Collapsed workflow state section (if `workflow_state.json` exists) — rendered via `format_collapsed_section()`.
2. Bot marker: `<!-- bot-agent-{role} -->`.
3. Display name: `*{Role Name} answer*`.

---

### Step 20: Post Agent Comment to GitHub

**Input:** `github_comment.md`, `TARGET_NUMBER`, `ROLE_NAME`.  
**Output:** New comment on the issue with the agent's answer.

Uses `peter-evans/create-or-update-comment@v4` to post the final comment. Mode is `create` (not replace) — answers accumulate as separate comments.

---

### Step 21: Minimize Acknowledgment Comment

**Input:** `ACK_COMMENT_NODE_ID` from Step 14.  
**Output:** The temporary "agent started" comment is minimized (collapsed) via GraphQL mutation.

Cleans up the acknowledgment comment from Step 13 by marking it as `OUTDATED` via the GraphQL `minimizeComment` mutation.

---

### Step 22: Process Split Plan (Issue Decomposition)

**Input:** `split_plan.json` (preferred) or `<splitPlan>...</splitPlan>` in `github_comment.md`. Only for `genius` role.  
**Output:** Sub-issues created under the parent issue.

Extracts the split plan from the genius agent's output and creates child issues via `issue_creation.py create`. Supports `auto_trigger` flag to automatically trigger agents on sub-issues.

**Split plan source priority:**
1. `split_plan.json` (pre-extracted by `agent_script.py`).
2. Parse `<splitPlan>` tags from `github_comment.md` (fallback for single-part messages).

---

### Step 23: Post Additional Message Parts

**Input:** `final_message_parts_count.txt`, `final_message_part_{N}.txt` files.  
**Output:** Additional comments on the issue for messages that exceeded GitHub's comment size limit.

If the agent's response was split into multiple parts (to stay within GitHub's ~65K char limit), posts parts 2 through N as separate comments.

---

### Step 24: Upload Logs as Artifact

**Input:** `*.log` files, `output/` directory.  
**Output:** GitHub Actions artifact named `openhands-task-logs` (7-day retention).

Always uploads logs for debugging purposes. Uses `actions/upload-artifact@v4`.

---

## 4. Job: `summarize-metrics`

**Trigger:** Issue closed (not a PR).  
**Purpose:** Aggregates metrics from all agent runs on the now-closed issue and posts a summary comment.

### Steps:
1. Checkout repository (with git-flow-ai cache if needed).
2. Setup Python.
3. Run `summarize_metrics.py` which scans all comments on the issue, extracts workflow states, and computes aggregate metrics.

**Input:** Issue number, comment history.  
**Output:** Summary comment posted on the issue.

---

## 5. Job: `subtask-completion-notifier`

**Trigger:** Sub-task issue closed (title starts with `[Sub-task`).  
**Purpose:** Notifies the parent issue's genius agent that a sub-task is complete.

### Steps:
1. Extract the 1-based index `N` from the title `[Sub-task N/M]`.
2. Resolve the parent issue number from the `parent-{N}` label on the sub-task.
3. Post a comment `<!-- bot-subtask-complete: {zero-index} -->` on the parent issue.

The resolver maps this token directly to the `genius` agent (no `@@genius` needed — the token is one of the machine trigger tokens the CI gate recognizes). Genius detects the marker in the comment and runs sub-task orchestration mode.

**Input:** Issue title, issue labels.  
**Output:** Comment on parent issue triggering genius agent to evaluate completion status.

---

## 6. Concurrency Model

```yaml
group: ${{ github.workflow }}-${{ github.event_name == 'issues' && format('-issue-{0}', github.event.issue.number) || format('-run-{0}', github.run_id) }}
cancel-in-progress: false
```

- For `issues` events: One concurrent run per issue number.
- For `workflow_call` events: One concurrent run per workflow run ID.
- `cancel-in-progress: false` — older runs are NOT cancelled (all agent runs must complete).

---

## 7. Permissions

| Permission | Level | Purpose |
|-----------|-------|---------|
| `contents` | `write` | Push branches, create PRs, checkout repos |
| `pull-requests` | `write` | Create/update PRs |
| `issues` | `write` | Post comments, manage labels |

---

## 8. Data Flow Diagram

```
GitHub Event
    │
    ▼
┌─────────────────────┐
│ Normalize Event     │ → TARGET_NUMBER, TRIGGER_BODY, EVENT_TYPE
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Resolve Config      │ → ROLE_NAME, COMPLEXITY, PROMPT_LOCATION, etc.
│ (resolve_config.py) │
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Loop Guards         │ → SKIP_AGENT (loop detection + human ping guard)
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Circuit Breaker     │ → CIRCUIT_BREAKER_TRIPPED (threshold check)
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Summarizer Gate     │ → SUMMARIZER_NEEDED (comment count threshold)
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Acknowledgment      │ → Comment "agent started"
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Query Context       │ → /tmp/ready_prompt.md
│ (query_context.py)  │
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Run Agent           │ → console.log, final_message.txt, workflow_state.json
│ (agent_script.py)   │
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Clean Comment       │ → github_comment.md (truncated, metrics preserved)
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Post to GitHub      │ → New issue comment with agent answer
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Post-Processing     │ → Split plan → sub-issues, multi-part messages, logs
└─────────────────────┘
```

---

## 9. Key Files Referenced by This Workflow

| File | Purpose |
|------|---------|
| `.github/scripts/resolve_config.py` | Parses trigger body, resolves agent role, complexity, prompt, skills |
| `.github/scripts/agent_script.py` | Core agent execution — loads prompt, runs OpenHands SDK, produces output |
| `.github/scripts/query_context.py` | Builds query context from issue + comments + workflow state |
| `.github/scripts/issue_creation.py` | Creates sub-issues from split plans |
| `.github/scripts/summarize_metrics.py` | Aggregates metrics across agent runs on a closed issue |
| `.agents/prompts/{role}.md` | Role-specific prompt templates |
| `skills/{name}.md` | Skill files loaded as additional context |
| `agent-config.yaml` | Layered config: providers → LLMs → agents |

---

## 10. Environment Variables Summary

### Input Variables (from workflow inputs/secrets)

| Variable | Source | Default | Purpose |
|----------|--------|---------|---------|
| `AGENT_CONFIG_PATH` | `inputs.agent_config_path` | `agent-config.yaml` | Path to agent configuration YAML |
| `CIRCUIT_BREAKER_THRESHOLD` | `inputs.circuit_breaker_threshold` | `15` | Max agent calls before circuit breaker trips |
| `SUMMARIZER_THRESHOLD` | `inputs.summarizer_threshold` | `100` | Comment count before summarizer triggers |
| `PIN_COMMENT` | `inputs.pin_comment` | `false` | Pin every agent step output comment for verbatim context injection |
| `PYTHON_VERSION` | `inputs.python_version` | `3.13` | Python runtime version |
| `ENABLE_UV_CACHE` | `inputs.enable_uv_cache` | `true` | Enable uv dependency caching |
| `OPENROUTER_API_KEY` | `secrets.OPENROUTER_API_KEY` | (required) | Default LLM API key |
| `GH_PAT` | `secrets.GH_PAT` | (required) | GitHub token for API access |

### Output Variables (set during execution)

| Variable | Set By | Purpose |
|----------|--------|---------|
| `TARGET_NUMBER` | Step 4 | Issue number being processed |
| `TRIGGER_BODY` | Step 4 | Content that triggered the workflow |
| `TRIGGER_AUTHOR` | Step 4 | Author of the trigger |
| `EVENT_TYPE` | Step 4 | `issues`, `issue_comment`, or `workflow_call` |
| `ROLE_NAME` | Step 8 | Selected agent role |
| `COMPLEXITY` | Step 8 | Task complexity level |
| `PROMPT_LOCATION` | Step 8 | Path to prompt template |
| `AGENT_SKILLS` | Step 8 | Comma-separated skill file names |
| `SKIP_AGENT` | Steps 8-10 | Whether to skip agent execution |
| `CIRCUIT_BREAKER_TRIPPED` | Step 10 | Whether circuit breaker activated |
| `SUMMARIZER_NEEDED` | Step 11 | Whether summarizer should run |
| `HAS_COMMENT` | Step 18 | Whether a comment was generated |
| `ACK_COMMENT_ID` | Step 13 | ID of acknowledgment comment |
| `AGENT_QUERY_PATH` | Step 15 | Path to prepared query context |

---

## 11. Bot Marker Conventions

Every comment posted by CI (any non-human sender) carries a **universal bot
marker** on its first line:

```
<!-- bot-workflow-terminal -->
```

Agent identity is carried by a **second, role-specific marker** retained on
comments where consumers need to know which agent wrote it:

```
<!-- bot-agent-{role} -->
```

### Emitter contract

| Emitter | Universal marker | Role marker |
|---------|------------------|-------------|
| Agent answer comment (Step 20) | line 1 | after state section |
| Acknowledgment comment (Step 13) | line 1 | line 2 |
| Circuit-breaker comment (Step 10) | line 1 | line 2 (genius) |
| Summarizer trigger (Step 12) | line 1 | line 2 (summary) |
| Terminal skip comment | line 1 | - |
| Multi-part message posts (Step 23) | every part, line 1 | - |
| Sub-task completion notification | present | - |
| Python emitters via `github_client.post_comment()` | injected by `ensure_bot_marker()` | - |

`github_client.ensure_bot_marker(body)` is the single injection point for the
Python side; it is idempotent (a body that already starts with the marker is
returned unchanged). The workflow side references the top-level `env:`
constant `BOT_MARKER`.

### Classification (`resolve_config`)

`parse_trigger.is_bot_comment()` classifies a trigger as bot-generated when
the **first 5 lines** contain:

1. the universal marker (primary), or
2. a legacy `<!-- bot-agent-` marker (fallback, for pre-existing issues).

Agent-identity consumers (`loop_guard.py`, the workflow's `CALLER_AGENT`
extraction, circuit-breaker/summary counting, `query_context.py`) rely only on
the role marker and are unaffected by the universal marker.

CI plain-mention triggers — a universal-marker comment whose body is only an
`@@mention` plus HTML-comment markers (e.g. the sub-task completion
notification `@@genius <!-- subtask-complete: N -->`) — route to the mentioned
agent even when an active workflow dispatch exists in the comment history,
matching the pre-marker behavior of their untagged predecessors.

### Follow-up (out of scope)

Once the flat marker has rolled out across live issues, the universal marker
should be renamed to a per-agent tag (`<!-- bot-workflow-{agent} -->`) — the
agent name is available at every emitting site (`ROLE_NAME` /
`workflow_state.agent`), making this a mechanical one-line-per-side change.
