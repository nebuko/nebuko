# Red-Test Review Loop — what code its tests run on

`red-test-review-loop` is the TDD workflow that turns a plan into failing
tests: `write_tests → git_diff → run_tests → review`. The engine, not the
agent, runs `git_diff` and `run_tests`, so review judges evidence the agent
did not produce.

This page explains **which tree** `run_tests` executes against, why it used to
disagree with CI, and how to read the `branch:` line in the run log.

---

## What code the tests run on

Script steps run on the session branch (`checkout: session`, the default since
issue #1327): the engine fetches `origin`, detaches onto the issue's
`feature/issue-<n>-*` branch, and — when asked — merges `origin/main` into that
detached head.

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

## `merge_main` manifest flag

The merge is on by default. `.nebuko/nebuko.yaml` sets it repo-wide:

```yaml
merge_main: true   # default when undeclared
```

- **Default: on.** With no manifest key the engine merges `origin/main` into
  the detached branch head before the step's command runs.
- A step opts out with an `env` block on its `script`:

  ```yaml
  script:
    command: "sh"
    env:
      NEBUKO_MERGE_MAIN: "0"
  ```

  Both `git_diff` steps do: a merge would pull `main`'s changes into the diff
  and hide the change under review. `NEBUKO_MERGE_MAIN=1` forces the merge on.
- The merge uses `git -c user.name=nebuko -c user.email=nebuko@invalid merge
  --no-edit --no-ff origin/main`, so it works on a runner with no git identity
  configured.

`red-test-review-loop`'s `run_tests` step relies on the manifest default;
`dev-review-loop`'s and `red-test-review-loop`'s `git_diff` steps opt out.

## Merge conflicts

If the branch and `origin/main` changed the same lines, the merge cannot
complete. The engine then:

1. runs `git merge --abort`, leaving the checkout clean on the branch head;
2. logs `evidence checkout: <branch> conflicts with origin/main; testing the
   branch head`;
3. still runs the step, so the red tests still produce evidence.

A conflicted run therefore tests the branch head, not the merged tree. Its
counts can differ from CI's — the branch needs `main` merged in (or a rebase)
before the two agree.

## Reading the `branch:` line

The step's posted comment names the tree it tested, on its `branch:` line:

```
branch: `feature/issue-1344-x` · head: `0123456789ab` · merge of <branch head sha> into <origin/main sha>
```

or, when the flag is off or the merge conflicted:

```
branch: `feature/issue-1344-x` · head: `0123456789ab` · branch head <branch head sha>
```

Compare that line with CI's log for the same pull request:

- Same branch head **and** same `origin/main` sha → the `failed / passed /
  subtests` summary should match CI's.
- Different `origin/main` sha → different counts are expected and legitimate;
  `main` moved between the two runs (tests added, removed, or fixed).
- `branch head …` in a `run_tests` comment → the merge did not happen (flag
  off or conflict); treat the extra failures as possibly already-fixed-on-
  `main` before charging them to the change.
