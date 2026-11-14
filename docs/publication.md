# Publication: how the engine turns agent work into a PR

Every approved write step must end with its work **published**: committed,
pushed, and checkpointed in a pull request. The engine owns the push and the
PR — the agent's token is deliberately not allowed to push (`denied by rule
git-push` in the logs is expected).

## How it works

1. **Commit** — the agent commits its work (or the engine's `ensure_pr`
   fallback commits the dirty tree when the run winds down).
2. **Push** — `ensure_pr` pushes the branch to origin. This includes work the
   agent already committed but could not push: committed-but-unpushed plain
   work is pushed as-is, preserving the agent's commit message and history.
3. **PR** — if no open PR exists for the branch, one is opened. The PR body
   contains `Closes #<TARGET_NUMBER>`, which links it to the triggering issue.

`.github/workflows/*` deltas are the exception: the agent token has no
`workflow` scope, so such changes are never pushed. They are delivered as a
`git apply`-able patch — a comment on the PR (or on the originating issue when
the change is workflow-only) plus a staged artifact (issue #1080).

### The publication guard

After an approved write step the guard (`run_agent._guard_publication`) calls
`check_publication` and, when the work is unpublished, one of three rescue
outcomes is recorded:

- `fallback_pr` — `ensure_pr` published the work (push + PR, or an existing PR
  was found). Success.
- `fallback_patch` — the delta was workflow-only; the patch was delivered as a
  comment/artifact. Success for that delta class.
- `fallback_failed` — the fallback could not publish anything. This is a real
  failure and is reported with the fallback's terminal `message` as detail.

## How to use it

As an agent you do nothing special: commit your work with a clear message and
end the step. Do **not** push — the engine pushes. Work that was already
committed but unpushed at guard time is picked up automatically: the branch is
pushed as-is and the PR is opened, linked to the issue via `Closes #N` in the
body. Dirty (uncommitted) changes are committed by the fallback with a
`WIP checkpoint` message.

## Failure modes

Each `> ⚠️ **Publication guard:**` warning line and its remediation:

- **…the automatic PR fallback failed. Error: …** — `ensure_pr` raised (git or
  `gh` failure). Inspect the quoted error; typical causes are a missing git
  remote, an unresolvable base ref, or a `gh` write that was dropped.
- **…the step finished approved with unpublished work — the automatic PR
  fallback failed** — the fallback returned without publishing and without an
  honest terminal state (pre-#1180 this could show the misleading detail
  `"workflow patch delivered"`). Check the branch and the base ref.
- **…nothing to publish** — benign: `check_publication` found a clean, pushed
  branch (or a benign skip such as no remote / detached HEAD).

## Troubleshooting

**`message = "nothing to publish: no plain delta and no PR opened"`**
`check_publication` judged the work unpublished (e.g. `unpushed_commits` /
`missing_pr`), but by the time `ensure_pr` inspected the branch and the dirty
tree there was nothing publishable left. Inspect the branch's unpushed state
against the resolved base ref — either the state changed between the check and
the fallback (benign race) or the base ref resolution differs between the two.
