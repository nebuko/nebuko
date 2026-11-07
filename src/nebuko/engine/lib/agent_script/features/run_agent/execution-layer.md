---
title: "Execution layer"
audience: contributor
sidebar_group: "Concepts"
sidebar_label: "Execution layer"
sidebar_order: 24
---

# Execution layer

The agent execution layer is the `agent_script` package
(`engine/lib/agent_script/`). Entry point `agent_script.py` orchestrates a
chain of features: `load_prompt` → `resolve_llm_config` → `run_agent` →
`build_output` (with `collect_metrics`, `ensure_pr`).

## Supervised execution — `run_agent`

The conversation runs via the async `arun()` API of the OpenHands SDK so that
a **watchdog** can call `conversation.interrupt()` to cancel the in-flight
task mid-LLM-call (issue #489). Timeouts are budget-based:

| Knob | Env var | Value in this repo |
|------|---------|---------|
| GHA hard stop | step `timeout-minutes` | 30 min |
| Main run budget | `AGENT_SOFT_TIMEOUT_MINUTES` | 20 min |
| Wind-down budget | `WIND_DOWN_TIMEOUT_MINUTES` | 10 min |

When the soft budget elapses the agent is **not** hard-killed by the GitHub
Actions timeout: it receives a **wind-down prompt** asking it to conclude its
work (post its answer, dispatch, and checkpoint any code) within the wind-down
budget. A gracefully timed-out run feeds the recovery-log pipeline, so the
next run resumes from a fresh session (see ADR-0002, issue #639).

## Deterministic PR fallback — `ensure_pr`

Guarantees the "checkpoint the work in a PR" requirement even if the wind-down
run is uncooperative — a pure git/gh subprocess sequence, no LLM involved:

1. Split the delta by path — `.github/workflows/*` is **patch-only**.
2. Commit the non-workflow working-tree changes (if any).
3. Push the current branch to `origin`.
4. Open a PR if none exists for the branch (idempotent).
5. Deliver the workflow delta as a patch a human applies.

Step 1/5 exists because the agent's token has no `workflow` scope, so GitHub
rejects any push carrying `.github/workflows/*`; before #1080 that rejection
failed the whole fallback and the work died with the runner. The split is a
**pre-check**, not reject-text parsing: because the pushed delta is guaranteed
free of workflow paths, the push cannot be refused for scope — so a mixed diff
produces a PR with the code and a patch for the workflows, and a workflow-only
diff produces a patch and no push at all. The patch is a comment (on the PR,
or on the originating issue when there is no PR) holding a fenced `diff` block
plus the one-line `git apply workflow-patch.diff` command; over the repo's 60k
comment policy the diff is staged as the `workflow-patch.diff` run artifact and
linked instead (`src/nebuko/engine/lib/agent_script/features/ensure_pr/workflow_patch.py`).
Unpushed commits that already carry workflow files are rebuilt from the merge
base (`reset --mixed` + re-commit, pushed with `--force-with-lease`) so the
pushed history stays clean too. The workflow files are discarded only *after*
delivery succeeded.

## Recovery trace extraction

`extract_recovery_trace` captures the tail of an interrupted run so a resumed
or debugged run knows exactly where the previous one stopped.

## Team context injection — `team_context`

`resolve_config/features/team_context/team_context.py` assembles the two shared
sections injected into **every** agent's system prompt (issue #476):

- the **generic exit rules** (mandatory feedback + exactly-one-sentinel), moved
  out of the individual prompt files so each built prompt contains one copy;
- a generated **"Your Team"** block — the agent's own role and its teammates'
  roles from the live config roster, the delegation rule (`call_agent`, not
  `@@`), the call-back reminder, and a caller line when the run was bot-invoked.

It is pure text generation over the parsed `AppConfig`; an unreadable roster or
an unknown agent degrades to no team section rather than failing the run (same
contract as `config.load_agent_names`). See
[agents-and-skills.md — Team Context Injection](../../../../../../../docs/agents-and-skills.md#team-context-injection).

## Long-output splitting — `message_splitter`

GitHub comment bodies have a size limit; `split_message` splits long final
messages at paragraph → line → sentence boundaries (hard split as last resort),
keeping each part under the threshold. The threshold comes from
`CommentSizePolicy.max_comment_chars` (default 60,000; issue #913) — a single
source of truth so `build_output`'s debug part files and the publish seam's
multi-comment post never disagree. At publish time `split_comment_body` packs
the answer into up to `CommentSizePolicy.max_parts` comments (default 4), with
the state-bearing header on the first part; parts 2..N are posted as
continuation comments and grouped back onto the state comment on re-read. Only
part 1 keeps the trigger payload — continuation parts are stamped
`<!-- bot-answer-continuation -->` and their payload is escaped, so a split
answer triggers the engine once (issues #994, #1002).
