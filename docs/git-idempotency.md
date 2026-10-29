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

1. Fetches the base branch only (`git fetch --no-tags origin <base>`), then
   the full history when the checkout is shallow (`actions/checkout` clones
   at depth 1, which makes a later merge fail with "unrelated histories").
2. **Asks whether the merge is needed**:
   `git merge-base --is-ancestor origin/<base> <branch>` — if the base is
   already contained in the branch, nothing is merged and the result is
   reported as *already up to date*.
3. Otherwise it creates `feature/issue-<n>-<slug>` from the base, or merges
   `origin/<base>` into the existing branch.
4. A merge conflict is left in progress for the agent: the prompt opens with
   a fix-first note listing the conflicted files, and the engine refuses to
   publish while conflicts remain. Any other merge failure fails the run.

## Idempotency guard

The engine keeps no memory between runs, so it asks git instead. One
content-level guard lives in `src/nebuko/engine/lib/git/idempotency.py`
(`GitIdempotency`):

- **Merge no-op skip** — `base_already_merged(branch, base)` runs
  `git merge-base --is-ancestor origin/<base> <branch>`. Exit 0 means the
  merge would be a no-op, so it is skipped. The skip itself saves nothing (a
  no-op merge creates no commit); its value is the *already up to date*
  signal, which tells the prompt that the previous rounds' work is present.

The other engine git actions need no guard, because each is already safe
to repeat:

- a `git push` of an up-to-date branch does nothing;
- the engine commits only when something is staged or a merge is pending;
- `ensure_pr` looks the branch's PR up before it creates one;
- a `.github/workflows/*` change the base already carries diffs empty
  against the base, so no patch is built or delivered.

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
- **A workflow patch keeps reappearing in comments** — the base does not
  carry the change yet: a patch is delivered only while its diff against
  `origin/<base>` is non-empty. Apply it on the base branch (`git apply
  workflow-patch.diff`, committed with a `workflow`-scoped token).
