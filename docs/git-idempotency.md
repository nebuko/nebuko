# Git idempotency: no repeated merges, no re-applied patches

## Symptom / overview

A run reported that the engine "re-applied an already-committed patch because
of a rebase". There is no literal `git rebase`: what looked like one is the
engine's branch preparation, which used to merge `origin/<base>` into the
feature branch on **every** issue-triggered run without checking whether the
merge was needed. When the base advanced, the redundant merge (or its
conflict note) made already-committed work look absent, and the agent
re-applied a patch that was already in the branch.

The fix is twofold: the merge is skipped when the base is already contained
in the branch, and a content-level guard detects a patch that is already
present in the tree before it is delivered again.

## How branch preparation works per run

Each run, the engine scaffold (`BranchScaffold`, in
`src/nebuko/engine/lib/git/branch_scaffold.py`) prepares the dev branch:

1. **Fetch the base** — the base branch (`main`, or `GITHUB_BASE_REF`) is
   fetched from `origin`.
2. **Ancestor check** — `git merge-base --is-ancestor origin/<base>
   <branch>`. If the base is already contained, the branch is up to date and
   nothing else happens: no checkout, no merge. The result carries
   `already_up_to_date=True` with detail `"<branch> already contains
   origin/<base>"`.
3. **Merge only when the base actually advanced** — `git merge --no-edit
   origin/<base>` on the feature branch.
4. **Conflict is data, not a crash** — a conflicted merge is aborted and
   returned as a structured result with a fix-first note for the agent; the
   run continues.

## Idempotency guards

The shared guard is `GitIdempotency`
(`src/nebuko/engine/lib/git/idempotency.py`). Three checks, all plain git
plumbing:

| Guard | Command | Meaning |
|---|---|---|
| merge no-op skip | `git merge-base --is-ancestor origin/<base> <branch>` | exit 0 → the merge would be a no-op, skip it |
| already applied | `git apply --check --reverse -` (patch on stdin) | clean reverse-apply → the patch is already in the tree |
| clean applicability | `git apply --check -` (patch on stdin) | the patch would apply cleanly if it were needed |

The workflow-patch delivery path
(`src/nebuko/engine/lib/agent_script/features/ensure_pr/workflow_patch.py`,
`patch_is_already_applied`) uses the reverse-apply check: when the delivered
workflow patch is already present, the comment is annotated **"already
applied — no action needed"** instead of re-issuing the bare
`git apply workflow-patch.diff` command.

## What the engine records

Skip outcomes are recorded in the persisted state's git history
(`GitInfo.history`, in
`src/nebuko/engine/lib/workflow_state/sub_domains/git_info/git_info.py`)
with the status `already_applied`, so the agent prompt can state that the
change already exists instead of guessing. Read a history entry as:

- `create` — the feature branch was created from the base.
- `rebase` / merge — the base was merged into the branch.
- `already_applied` — nothing was done: the base was already contained
  (merge skip) or the patch was already present (reverse-apply hit).
- `conflict` — the merge conflicted; the note in the state carries the
  fix-first instructions.

## Troubleshooting

- **A change looks duplicated after a base update** — check the state's git
  history: an `already_applied` entry means the engine detected the
  duplication and skipped. If a real duplicate exists, look for a
  non-engine path (a manual apply, a second agent round) that bypassed the
  guards.
- **A change looks missing after a base update** — the base advanced and the
  merge hit a conflict; look for the `conflict` entry and its fix-first
  note. The work is not lost: it is on the feature branch, the merge is
  simply unresolved.
- **The patch comment says "already applied"** — the workflow files in the
  tree already match the patch; no action is needed. Verify with
  `git apply --check --reverse workflow-patch.diff` on a clean checkout: it
  exits 0 exactly when the patch is present.
