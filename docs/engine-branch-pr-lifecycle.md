# Engine branch & PR lifecycle

Every dev turn, the engine records what it did to the repository and what it
planned next. This section is the user-facing contract for that record.

## How it works

The engine owns the dev branch end to end:

- On the first dev turn it creates `feature/<issue_number>-<slug(title)>`
  from `origin/main`.
- On every turn it rebases the branch on top of `origin/main` before the
  agent runs, so the dev role always sees a clean, up-to-date base.
- At session end it records one history entry per engine git action, with the
  short sha of the commit it made (if any) and the turn the action belongs to.

## WHEN/THEN rules

```
WHEN engine start for dev role
  IF there is no branch in workflow_state
    THEN it creates feature/<issue_number>-<slug(title)> from origin/main
    AND it opens the PR titled "<issue_title> (#<issue_number>)"
  IF the rebase contains a conflict
    THEN it logs a `conflict` entry in state.git.history
    AND it injects a fix-first note into the dev prompt
```

## Engine-made commits

Commits made by the engine (scaffold, auto-commit) carry the message
`chore: engine: <action>`; the agent's own commits are never rewritten, only
rebased.

## Engine-made commit messages

| message | meaning |
|---------|---------|
| `chore: engine: scaffold` | branch created from origin/main |
| `chore: engine: rebase`   | branch rebased on top of origin/main |
