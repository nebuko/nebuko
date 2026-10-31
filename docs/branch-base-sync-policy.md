# Branch base sync policy: merge only on conflict, never at publish time

Issue: [#1242](https://github.com/nebuko/nebuko/issues/1242) — PRs polluted by
routine base merges (root cause of the PR #1203 workflow reverts).

## Usage — what you see as an agent or human

**As the dev agent, at turn start.** The engine scaffolds your branch:

- New branch → created from the latest `origin/<base>`. Unchanged.
- Existing branch → the engine runs a **read-only** conflict probe
  (`git merge-tree --write-tree`). Two outcomes:
  - **No conflict** → nothing happens. The branch is *not* merged with the
    base; you work on it as-is. Your PR diff contains only issue work.
  - **Conflict** → the engine performs the merge and hands the conflicted
    paths to you in the "Branch conflict — fix first" prompt section. Resolve
    only the listed files (remove markers, `git add`), commit, push plainly.
    This is the *only* path where the base ever enters your branch.

**As a human, at publish time.** The engine never merges the base into the
branch before pushing. It pushes the agent's commits plainly, after a
read-only conflict probe against `origin/<base>`:

- Clean → normal publish.
- Main moved and conflicts exist → the publish succeeds and one PR comment is
  posted: *"main has moved, conflicts in X — re-call the dev agent"*. The
  next dev run's turn-start probe (above) resolves them. The publish is
  **never blocked** by this.

**Workflow files.** `.github/workflows/*` content that is identical to
`origin/<base>` is treated as main's own state arriving via merge and is
**kept** — it is never reverted as agent work. Only a genuine workflow *delta*
goes through the patch flow. If a push is rejected by GitHub for missing
`workflow` scope, the engine **stops and reports** with an actionable message;
it never silently reverts.

## Mechanics — where it plugs into the codebase

| Point | Before | After |
|---|---|---|
| Turn start, existing branch | `BranchScaffold._merge_origin` always merged `origin/<base>` | Dry-runs `git merge-tree --write-tree`; merges only on conflict |
| Publish | `ensure_pr._sync_base_before_push` fetched + merged the base before pushing | Function removed; plain push + read-only `merge-tree` check + PR comment on conflict |
| Workflow revert | `revert_workflow_changes` restored every workflow path from the branch's previous content | Workflow paths whose tree content equals `origin/<base>:` are excluded from the revert set |

- `src/nebuko/engine/lib/git/idempotency.py` — `GitIdempotency.has_base_conflict(branch, base)`:
  read-only `git merge-tree --write-tree origin/<base> <branch>`, returns the
  conflicted paths (empty list = clean). `base_already_merged` is removed.
- `src/nebuko/engine/lib/git/branch_scaffold.py` — `_merge_origin` probes
  first, merges only on conflict; `_prepare_branch` routing is unchanged.
- `src/nebuko/engine/lib/agent_script/features/ensure_pr/ensure_pr.py` —
  `_base_conflict_paths(branch, base)` (local read-only merge-tree helper,
  same output contract) plus the plain-push publish flow;
  `_sync_base_before_push` / `_base_already_in_branch` are removed.

## Why (invariants)

- Agent history is **never rewritten**: no rebase, no force-push, ever.
- PRs contain only issue work: routine base merges were what dragged main's
  changes into the PR diff (and let the workflow-revert path *undo* main's
  workflow updates on PR #1203).
- GitHub's three-dot diff handles a lagging base fine: a branch that does not
  contain the latest main still merges cleanly on GitHub.
