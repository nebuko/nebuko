#!/usr/bin/env bash
# Remove the session handoff before the human review gate (issue #965).
#
# The file is deleted with a plain deletion commit, so it stays recoverable from
# the branch history. Idempotent: a missing file is a success (the gate check
# that follows is the step that must fail if the removal did not happen).
set -uo pipefail

HANDOFF_PATH=".nebuko/handoff.md"

if [ ! -f "$HANDOFF_PATH" ]; then
  echo "handoff cleanup: $HANDOFF_PATH already absent — nothing to do"
  exit 0
fi

git rm --quiet -- "$HANDOFF_PATH" || { echo "handoff cleanup: git rm failed"; exit 1; }

git -c user.name=nebuko -c user.email=nebuko@users.noreply.github.com \
  commit --quiet -m "chore: remove session handoff" -- "$HANDOFF_PATH" \
  || { echo "handoff cleanup: commit failed"; exit 1; }

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if git remote get-url origin >/dev/null 2>&1; then
  git push origin "$BRANCH" \
    || { echo "handoff cleanup: push failed"; exit 1; }
fi

echo "handoff cleanup: removed $HANDOFF_PATH on $BRANCH (recoverable from history)"
