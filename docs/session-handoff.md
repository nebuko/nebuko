# Session handoff

When a run ends early (soft kill, crash, CI timeout) the next run starts
without a reliable record of what was done and thought. The `handoff` tool is
that record: any agent — including the read-only roles (architect, review, po)
— writes a structured handoff document, and the next run's prompt starts from
it.

## Calling the tool

The `handoff` tool takes structured fields, never free text:

| Field | Meaning |
| --- | --- |
| `completed` | Concrete deliverables produced so far. |
| `state` | Branch / PR / current status. |
| `verified_facts` | Claims you actually checked, each with its evidence (command run, file, test output). |
| `hypotheses` | Unverified guesses, kept apart from the facts. |
| `blockers` | What is stuck and why. |
| `next_steps` | Ordered next actions. |
| `plan_changes` | What changed since the previous version of the document, and why. |
| `fold` | Optional. `true` means "stop here, I want a fresh start" (see below). |

Every call **overwrites** the document entirely — nothing is appended, so
stale claims cannot accumulate across runs. `plan_changes` is overwritten with
the rest of the document; previous versions remain reachable in git history.

**Never put secrets, tokens, credentials or private customer content in the
document.** The tool description and the rendered file both say so. The file is
committed to the branch and pushed.

## Storage

The engine does the git work, so an agent needs no worktree write access:

- The document lives at `.nebuko/handoff.md` (Markdown, fixed headings).
- Each call stages **only** that path, commits it as
  `chore: update session handoff` and pushes. Other edits in the worktree stay
  uncommitted — they belong to the agent's own commit.
- If HEAD is on a base branch (`main`/`master`) — the case for a read-only role
  before the dev step has ever run — the engine first creates and pushes
  `feature/nebuko-handoff-<UTCts>`. Dev reuses that checked-out branch
  (`feature/issue-<n>-<slug>`, created by the engine's branch scaffold) instead
  of creating another one, so the handoff and
  the work travel together.

## Consuming it

`build_prompt` injects a `## Previous session handoff` block after the step
inputs and before the user request, in the same style as the injected-skills
block. The block is framed explicitly: **verified facts are claims to re-check,
not truths** — re-run the command, re-read the file, re-check the test output
before relying on anything in it. Hypotheses are marked unverified.

If the file is absent (the cleanup step already ran, and a human asked for
another dev turn), the store revives the last committed version into the
worktree, uncommitted, before rendering the block. The restore is idempotent.

## Folding

`fold: true` saves the document and then ends the run as an incomplete
`agent_checkpoint` (`OutcomeStatus.AGENT_CHECKPOINT` →
`IncompleteReason.AGENT_CHECKPOINT`), instead of letting the agent keep working
or advance.

Routing reuses the existing soft-kill retry path: the step re-runs in place
while the per-step budget remains, and the workflow halts for a human once it is
spent (`MAX_SOFT_RETRIES`, `SOFT_RETRY_EXHAUSTED`). Folds and soft kills share
that budget, which is the **cap against folding out of hard tasks**.

## Cleanup and the review gate

The document is an internal artifact and must not reach the reviewer's
deliverable, so the `plan-dev-review-cmd` workflow runs, on the `approved`
route only:

1. `handoff_cleanup` (`scripts/handoff_cleanup.sh`) — a plain deletion commit
   plus push; idempotent when the file is already gone.
2. `handoff_gate_check` (`scripts/handoff_check.sh`) — fails the workflow with
   an actionable message if the file is somehow still present.

Cleanup runs *after* the reviewer agent, because a `rejected` review sends the
work back to `implement`, which still needs the document.

## Branch and PR base sync

The engine keeps a PR branch on top of the freshest base by merging
`origin/<base>` into it at two points:

1. **Pre-agent** — `BranchScaffold` fetches the base and merges it while
   creating (or resuming) the feature branch, before the agent runs.
2. **Publish-time** — before the final push, `ensure_pr` fetches
   `origin/<base>` again and merges it into the branch. Main can advance
   while the agent works; this second merge closes that gap. Both merges are
   skipped when the base is already contained in the branch
   (`git merge-base --is-ancestor`), so re-runs stay idempotent.

**What a PR contains.** GitHub's PR diff is a three-dot diff against the
merge base. When main advances after the pre-agent merge — or when earlier
work on the same issue was squash-merged into main and the branch re-applies
it — the branch can carry hunks identical to main, and the PR shows them as
"changes already in main". The publish-time merge moves the merge base up to
the freshest main, so base-identical hunks no longer appear in the PR.

**Failure and recovery.** Publishing is fail-closed: if the base fetch
fails, the merge fails, or the merge leaves conflicts, `ensure_pr` aborts
the merge (where applicable) and refuses to push — the PR is not opened or
updated with stale content. The agent's work is already committed locally on
the branch, so nothing is lost: re-running the workflow re-syncs the branch
against the base and retries the publish; a conflict is handed to the agent
as its fix-first work, the same way the pre-agent scaffold does.

**Accepted caveat:** the handoff stays in the branch history after cleanup
unless the merge is squashed. That is intentional — it is what makes the
document recoverable — but it means the file's contents are part of the branch's
permanent record.
