#!/usr/bin/env bash
# Gate check: fail the workflow if the session handoff is still present (#965).
#
# Runs after the cleanup step and before human review. A leftover
# .nebuko/handoff.md means the cleanup did not happen, so the gate must not
# open: the document is an internal artifact and must not reach the reviewer.
set -uo pipefail

HANDOFF_PATH=".nebuko/handoff.md"

if [ -f "$HANDOFF_PATH" ]; then
  echo "handoff gate check FAILED: $HANDOFF_PATH is still present." >&2
  echo "Run scripts/handoff_cleanup.sh (or delete the file with a deletion commit and push) before requesting review." >&2
  exit 1
fi

echo "handoff gate check passed: $HANDOFF_PATH is absent"
exit 0
