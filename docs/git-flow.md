# Git flow: agents commit, the engine publishes

Agents never touch a remote. They work and commit on a branch the engine
prepared; after the agent's run the engine pushes the branch, delivers
workflow-file changes as a patch, and opens (or reuses) the pull request
linked to the issue.

## How it works

The publish pipeline is engine-owned end to end, in this order:

1. **Branch scaffold** — before the run, the engine creates or rebases the
   feature branch `feature/issue-<n>-<slug>` on the base branch and hands it
   to the agent as the working branch (`BranchScaffold`).
2. **Agent works and commits only** — the agent edits files, runs tests and
   commits with `git commit`. Any `git push` is denied by the engine's
   terminal deny-list before it reaches the shell: the statement is not
   executed and the agent receives an explanation instead (see below).
3. **Engine finalize** — after the run, `ensure_pr` (via the state phase's
   `_pr_finalize`):
   - commits any files the agent left uncommitted,
   - pushes the branch,
   - delivers changes under `.github/workflows/` as a `workflow-patch.diff`
     artifact (the agent token has no `workflow` scope, so they can never be
     pushed directly),
   - opens a pull request if none exists for the branch and links it to the
     issue with `Closes #N`.

`ensure_pr` is idempotent: re-running it finds the existing PR, pushes only
what is missing, and refreshes the patch — it is safe on every re-run.

## How to use it

**For agents:**

- Work on the branch the prompt gives you. Do not create branches.
- Commit your work locally with clear messages. Stage only the files you
  changed — never generated files, caches, or secrets.
- Never run `git push`, and never open or comment on pull requests. The
  engine pushes and opens the PR after your run.

**For operators:**

- A denied statement appears in the agent's tool output as a failed entry
  with the message: *"git push is disabled for agents: the engine commits
  leftovers, pushes the branch and opens the pull request after your run.
  Commit your work locally instead."* The batch continues with the remaining
  entries.
- Push and PR creation are driven by the engine's `GH_TOKEN` /
  `GITHUB_TOKEN` credentials (`GitSessionGuard` proves the auth before the
  session and re-proves it before each push).
- If finalize reports push-auth problems, check that the token is present,
  has `repo` (and, for workflow files, `workflow`) scope, and is not expired;
  then re-run — `ensure_pr` picks up where it stopped.

## Limitations & troubleshooting

- The deny-list is a regex check on each submitted statement, scoped to
  `git push`. Obfuscated invocations (e.g. through a script or an alias that
  itself pushes) are not caught; a git `pre-push` hook and credential
  isolation are recorded as future hardening.
- `gh pr create` and branch creation by agents are discouraged by
  instructions but not hard-blocked; the hard block covers `git push` only,
  per the issue's scope.
- If the agent sees deny messages but the branch still ends up unpushed,
  the publication guard recalls it for uncommitted changes only; anything
  push-related is the engine's job, and finalize's `ensure_pr` fallback
  handles the rest.
