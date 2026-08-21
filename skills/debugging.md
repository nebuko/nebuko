# Debugging Skill

Procedural playbook for investigating unusual agent behavior by analyzing past workflow run logs. Analysis is checkpointed in issue comments so a re-run can resume where it left off.

## 1. Resume-Aware Startup

Check whether partial checkpoint comments were already posted on this issue:

```bash
gh api /repos/${GITHUB_REPOSITORY}/issues/${TARGET_NUMBER}/comments --jq '.[] | select(.body | contains("bot-debug-checkpoint-"))' > /tmp/debug_checkpoints.json 2>/dev/null || true
grep -oP 'bot-debug-checkpoint-\K[0-9]+' /tmp/debug_checkpoints.json | sort -u > /tmp/analyzed_runs.txt
```

Skip every run ID in `/tmp/analyzed_runs.txt` in Pass 2. If all runs are analyzed, post a brief summary and finish.

## 2. Pass 1 — Overview and Triage (No Downloads Yet)

### 2a. Gather Issue Comments

```bash
gh api /repos/${GITHUB_REPOSITORY}/issues/${TARGET_NUMBER}/comments --jq '.' > /tmp/issue_comments.json
```

### 2b. Extract Run IDs

Each bot acknowledgment contains a line like:
`🤖 **{role_name} agent started** — [View workflow run logs](https://github.com/{owner}/{repo}/actions/runs/{run_id})`

```bash
grep -oP '\*\*([a-z-]+)\*\* agent started.*?/actions/runs/\K(\d+)' /tmp/issue_comments.json | sort -u > /tmp/all_run_ids.txt
```

### 2c. Build the Timeline

For each entry, compose a 1–2 sentence summary from the comment body (agent role, `AGENT_RUN_CONFIG` sentinel, final message).

### 2d. Identify the Drift Boundary

Determine where things went wrong (incorrect hand-off, error/exception, workflow-sequence deviation). Mark the run ID(s) around the drift, including **one run before** for full context.

### 2e. Post the Overview Comment

Tagged `<!-- bot-agent-debug -->`:

```markdown
## 🔍 Debug Analysis — Overview

| # | Role | Run ID | Summary |
|---|------|--------|---------|
| 1 | genius | 123456789 | Analyzed request, proposed architecture |

**Suspected drift boundary:** Run #123456789 ({role_name}) — {reason}

Deep dive starting from run #{prev_run_id} for full context.
```

## 3. Pass 2 — Deep Dive with Checkpoints

Process each remaining run sequentially from the drift boundary.

**Step 1 — Download the artifact** (note and skip if expired/missing):

```bash
gh run download {run_id} --name openhands-task-logs --dir /tmp/artifact_{run_id}
```

**Step 2 — Analyze the console log** (`cat /tmp/artifact_{run_id}/console.log`, in chunks via `head`/`tail`/`sed -n` for large logs). Focus on: tool-call sequences and arguments; error patterns (exceptions, non-zero exits); decision points that may have led astray; token/context overflow signs; deviation from the agent's instructions. Clean up afterwards: `rm -rf /tmp/artifact_{run_id}`.

**Step 3 — Post a checkpoint comment**, tagged `<!-- bot-debug-checkpoint-{run_id} -->`:

```markdown
### 🔎 Deep Dive — {role_name} (Run #{run_id})

**Artifact:** [Download link](https://github.com/{owner}/{repo}/actions/runs/{run_id})

**What happened:** {1-2 sentence summary}

**Findings:**
- {finding 1}

**Root cause hypothesis:** {if applicable}
```

Wait a few seconds between posts to avoid rate limiting.

## 4. Final Summary

Tagged `<!-- bot-agent-debug-conclusion -->`:

```markdown
## 🐛 Debug Conclusion

**Drift root cause:** {concise explanation}

**Affected runs:** #{run_id1}, #{run_id2}, ...

**Recommendation:** {how to prevent recurrence}
```

## Constraints

- Comment-only output: never modify code, commit, or push.
- Write comment bodies to a temp file and post with `-F body@/tmp/comment_body.txt` to avoid shell-quoting issues.
- To update an existing checkpoint instead of duplicating it, find its ID first, then PATCH:
  ```bash
  COMMENT_ID=$(gh api /repos/${GITHUB_REPOSITORY}/issues/${TARGET_NUMBER}/comments --jq '.[] | select(.body | contains("bot-debug-checkpoint-{run_id}")) | .id')
  gh api PATCH /repos/${GITHUB_REPOSITORY}/issues/comments/${COMMENT_ID} -F body@/tmp/comment_body.txt
  ```
- Artifacts have a 7-day retention; if unavailable, note it and proceed.