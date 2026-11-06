#!/bin/sh
# Oracle for issue #1356: the red-test loop's pytest run reports different
# numbers than CI on the same commit.
#
# It rebuilds, from pinned git objects only (no network, no "latest"), the two
# trees in play:
#   branch head  bc4625c  - what the loop's run_tests step checks out today
#   PR merge     545b2c9 + bc4625c - what GitHub's CI job checks out
# and runs the documented full command (AGENTS.md) on the tree the loop tests.
#
# Which tree the loop tests is read from the current checkout of the run_tests
# step, not hard-coded: `NEBUKO_MERGE_MAIN=1` in
# .nebuko/workflows/red-test-review-loop.yaml means the step tests the merge
# tree (CI shape). Before the fix it is absent, so the loop tests the branch
# head and every delta shows the symptom.
#
# Diagnostics go to stderr; the last stdout line is the JSON object.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(git -C "$here" rev-parse --show-toplevel)

# Pinned objects: the red-test branch head the loop tested and the main tip
# GitHub merged it into (see pinned.txt for provenance).
branch_head=bc4625ce597ec5b2b0bf87719aba9ec6222d87eb
main_tip=545b2c938b699117b4a73b22639b05c18ab98260

loop_workflow="$repo/.nebuko/workflows/red-test-review-loop.yaml"
if grep -q 'NEBUKO_MERGE_MAIN=1' "$loop_workflow" 2>/dev/null; then
  loop_tree=merge
else
  loop_tree=head
fi
echo "loop tests: ${loop_tree} tree" >&2

tmp=$(mktemp -d)
cleanup() {
  git -C "$repo" worktree remove --force "$tmp/head" >/dev/null 2>&1 || true
  git -C "$repo" worktree remove --force "$tmp/merge" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT INT TERM

git -C "$repo" worktree add --detach "$tmp/head" "$branch_head" >/dev/null 2>&1
git -C "$repo" worktree add --detach "$tmp/merge" "$main_tip" >/dev/null 2>&1
git -C "$tmp/merge" -c user.name=nebuko -c user.email=nebuko@invalid \
  merge --no-ff --no-edit "$branch_head" >/dev/null 2>&1

# The engine's own checkout is absent from a bare worktree, so the mirror-copy
# test in tests/engine/test_spec_tdd_analyze_output.py fails here exactly as it
# did in the loop run (the loop's checkout has no byte-identical mirror either).
measure() {
  dir=$1
  cd "$dir"
  uv sync --extra runtime --offline >/dev/null 2>&1 ||
    uv sync --extra runtime >/dev/null 2>&1
  collected=$(.venv/bin/python -m pytest --collect-only -q 2>/dev/null |
    grep -cE '^[^[:space:]].*::' || true)
  # STEP_OUTPUT is injected by the engine into agent/script steps; CI has none,
  # and leaving it set makes output-contract tests fail on both trees.
  env -u STEP_OUTPUT .venv/bin/python -m pytest tests src/nebuko/engine/lib \
    -q --tb=no >"$tmp/run-$(basename "$dir").txt" 2>&1 || true
  summary=$(grep -E '^[0-9]+ (failed|passed)' "$tmp/run-$(basename "$dir").txt" |
    tail -1)
  counts=$(printf '%s\n' "$summary" | awk '
    { gsub(",", "")
      for (i = 1; i < NF; i++) {
        if ($(i + 1) == "failed") f = $i
        if ($(i + 1) == "passed" && $i != "subtests") p = $i
        if ($(i + 1) == "subtests") s = $i
      }
      print f + 0, p + 0, s + 0 }')
  set -- $counts
  failed=$1
  passed=$2
  subtests=$3
  echo "  $(basename "$dir"): ${summary}" >&2
  echo "$failed $passed $subtests $collected"
}

head_stats=$(measure "$tmp/head")
merge_stats=$(measure "$tmp/merge")

case "$loop_tree" in
  merge) stats=$merge_stats ;;
  *) stats=$head_stats ;;
esac

set -- $stats
printf '{"failed": %s, "passed": %s, "subtests": %s, "collected": %s}\n' \
  "$1" "$2" "$3" "$4"
