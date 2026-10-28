# Git idempotency: why work is never re-applied

## Symptom

The engine appeared to "re-apply" a patch that had already been committed to
the feature branch, usually right after a base-branch update ("rebase").
Nothing in the engine literally rebased; the trigger was the branch
preparation step re-merging an unchanged base, which made committed work
look absent.

## How branch preparation works per run

On every issue-triggered run the engine (`BranchScaffold`, in
`src/nebuko/engine/lib/git/branch_scaffold.py`):

1. Fetches the base branch only (`git fetch --no-tags origin <base>`).
2. **Asks whether the merge is needed**:
   `git merge-base --is-ancestor origin/<base> <branch>` — if the base is
   already contained in the branch, nothing is merged and the result is
   reported as *already up to date*.
3. Otherwise it creates `feature/issue-<n>-<slug>` from the base, or merges
   `origin/<base>` into the existing branch.
4. A merge conflict is returned as data (a fix-first note in the prompt),
   never a crash.

## Idempotency guards

Two content-level guards live in
`src/nebuko/engine/lib/git/idempotency.py` (`GitIdempotency`):

- **Merge no-op skip** — `base_already_merged(branch, base)` runs
  `git merge-base --is-ancestor origin/<base> <branch>`. Exit 0 means the
  merge would be a no-op; the engine skips checkout and merge entirely.
- **Already-applied check** — `patch_already_applied(patch)` runs
  `git apply --check --reverse -` against the working tree. A reverse apply
  that succeeds means the diff is already present, so the change is
  reported instead of re-delivered.
- **Forward check** — `patch_applies_cleanly(patch)` runs
  `git apply --check -` for a plain applicability probe.

The workflow-patch delivery (`ensure_pr`) uses the already-applied check:
when the `.github/workflows/*` patch reverse-applies against the checkout,
the comment states *"already applied — no action needed"* instead of
re-issuing `git apply workflow-patch.diff`.

## What the engine records

Branch-scaffold outcomes land in the persisted state's
`current_context` (`dev_branch`, plus `dev_branch_up_to_date` and
`dev_branch_note` when the branch already contained the base) and in the
git sub-domain's action history (`workflow_state/sub_domains/git_info`),
whose statuses include `already_applied` for skipped/no-op actions. An
entry with status `already_applied` or a detail of
"`<branch>` already contains `origin/<base>`" means the engine verified the
work was present and deliberately did nothing.

## Troubleshooting

- **A change looks duplicated** — check `git log` on the feature branch for
  a merge commit re-introducing it; current builds skip that merge, so a
  duplicate implies the change was applied twice outside the engine.
- **A change looks missing after a base update** — check the state history
  for an `already_applied` entry: the engine saw the change as present. If
  `git merge-base --is-ancestor origin/<base> <branch>` fails, the base
  moved and the next run merges it normally; resolve any conflict as the
  prompt's fix-first note instructs.
- **A workflow patch keeps reappearing in comments** — confirm the change
  was actually applied where the comment targets (`git apply --check
  --reverse workflow-patch.diff` succeeds only when it is present).
