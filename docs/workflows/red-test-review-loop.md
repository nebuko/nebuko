# Red-Test Review Loop — what code its tests run on

`red-test-review-loop` is the TDD workflow that turns a plan into failing
tests: `write_tests → git_diff → run_tests → review`. The engine, not the
agent, runs `git_diff` and `run_tests`, so review judges evidence the agent
did not produce.

This page explains **which tree** `run_tests` executes against, why it used to
disagree with CI, and how to read the `tested:` line in the run log.

---

## What code the tests run on

Script steps start on the workflow's own checkout, so `run_tests` first calls
`.nebuko/scripts/use-session-branch.sh`. The script fetches `origin`, detaches
onto the issue's `feature/issue-<n>-*` branch, and then — when asked — merges
`origin/main` into that detached head.

The merge is what makes the run comparable with CI. `actions/checkout` on a
pull request checks out GitHub's **PR merge commit**: the branch head merged
into the current `main`. Testing the bare branch head instead means the run
also exercises every failure that `main` has already fixed, so review sees
dozens of failures that have nothing to do with the change under review.

With the merge, the workflow tests the same shape of tree CI tests:

```
origin/main ──┐
              ├──► local merge commit  ◄── pytest runs here
branch head ──┘
```

The merge commit is created **locally only**. It is never pushed, so the
feature branch and the PR history are untouched.

## `NEBUKO_MERGE_MAIN` flag

The merge is opt-in. Set `NEBUKO_MERGE_MAIN=1` in the environment of
`use-session-branch.sh`:

```sh
NEBUKO_MERGE_MAIN=1 sh .nebuko/scripts/use-session-branch.sh
```

- **Default: off.** Without the variable the script behaves exactly as before:
  fetch, detach onto the branch head, stop. Every other caller — including the
  `git_diff` step — keeps that behaviour.
- `git_diff` must **not** set the flag: a merge would pull `main`'s changes
  into the diff and hide the change under review.
- The merge uses `git -c user.name=nebuko -c user.email=nebuko@invalid merge
  --no-edit --no-ff origin/main`, so it works on a runner with no git identity
  configured.

`red-test-review-loop`'s `run_tests` step sets the flag; `dev-review-loop` and
the other script steps do not.

## Merge conflicts

If the branch and `origin/main` changed the same lines, the merge cannot
complete. The script then:

1. runs `git merge --abort`, leaving the checkout clean on the branch head;
2. prints `merge: conflict with origin/main; testing branch head`;
3. exits `0`, so the red tests still run and still produce evidence.

A conflicted run therefore tests the branch head, not the merged tree. Its
counts can differ from CI's — the branch needs `main` merged in (or a rebase)
before the two agree.

## Reading the `tested:` line

Every run prints exactly one line naming the tree it tested:

```
tested: merge of <branch head sha> into <origin/main sha>
```

or, when the flag is off or the merge conflicted:

```
tested: branch head <branch head sha>
```

Compare that line with CI's log for the same pull request:

- Same branch head **and** same `origin/main` sha → the `failed / passed /
  subtests` summary should match CI's.
- Different `origin/main` sha → different counts are expected and legitimate;
  `main` moved between the two runs (tests added, removed, or fixed).
- `tested: branch head …` in a `run_tests` log → the merge did not happen
  (flag missing or conflict); treat the extra failures as possibly
  already-fixed-on-`main` before charging them to the change.
