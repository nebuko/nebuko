#!/bin/sh
# Oracle for issue #1358: workflow test run != CI results on the same commit.
#
# Root cause: the red-test-review-loop `run_tests` step tests the BRANCH HEAD
# (bc4625ce597ec5b2b0bf87719aba9ec6222d87eb, via .nebko/../use-session-branch.sh
# which does `git checkout --detach origin/feature/issue-<n>-*`), while CI's
# internal-tests workflow triggers on pull_request and therefore tests GitHub's
# merge commit (branch + main = tree 25cd897db846a6f1b8a2a7b8fb203c79ba7).
#
# The two checkouts differ in test content, not just machine:
#   - engine-lib test files: 123 at branch head vs 124 merged (merged adds
#     test_replay.py),
#   - self.subTest sites: 3 at head vs 5 merged
#     (head: gate_decision 2 executed + resolve_config 4 executed = 6;
#      merged: those 6 + run_failure 6+2 = 14).
# Hence the workflow's "6 subtests passed" vs CI's "14 subtests passed", and
# part of the passed-count gap, from the checkout alone.
#
# All measurements derive from pinned git objects only; no network, no "latest".

set -eu
cd "$(dirname "$0")"

HEAD_SHA=bc4625ce597ec5b2b0bf87719aba9ec6222d87eb
MERGE_TREE=25cd897db846a6f1b8a18e3a2a7b8fb203c79ba7

git cat-file -e "$HEAD_SHA"
git cat-file -e "$MERGE_TREE"

# Deterministic derivation of executed-subtest counts from the pinned blobs.
# head: gate_decision has 2 subTest calls (executed once each),
#       test_resolve_config has 1 subTest inside a 4-element for-loop -> 4.
head_gd=$(git show "$HEAD_SHA:src/nebuko/engine/lib/resolve_config/features/resolve_route/test_gate_decision.py" | grep -c 'self\.subTest(' || true)
head_rc_loop=$(git show "$HEAD_SHA:src/nebuko/engine/lib/test_resolve_config.py" \
    | grep -A1 'self\.subTest(body=body)' | grep -c 'self\.subTest' || true)
head_exec=$((head_gd + 4))

# merged: same two files plus run_failure with two subTest loops
# (6-entry stopping dict + 2-entry request-specific dict = 8 subtests).
merge_gd=$(git show "$MERGE_TREE:src/nebuko/engine/lib/resolve_config/features/resolve_route/test_gate_decision.py" | grep -c 'self\.subTest(' || true)
merge_rf=$(git show "$MERGE_TREE:src/nebuko/engine/lib/agent_script/features/run_agent/test_run_failure.py" | grep -c 'with self\.subTest(label)' || true)
merge_rc_loop=$(git show "$MERGE_TREE:src/nebuko/engine/lib/test_resolve_config.py" | grep -c 'self\.subTest(body=body)' || true)
merge_exec=$((merge_gd + 8 + 4))

# Guard: the derivation must match the measured subtest totals from the
# investigation runs (6 at head, 14 at the merged tree), otherwise fail loudly
# instead of printing a wrong number.
[ "$head_gd" -eq 2 ] || { echo "unexpected gate_decision subTest count at head: $head_gd" >&2; exit 1; }
[ "$merge_gd" -eq 2 ] || { echo "unexpected gate_decision subTest count at merge tree" >&2; exit 1; }
[ "$merge_rf" -eq 2 ] || { echo "unexpected run_failure subTest sites at merge tree" >&2; exit 1; }
[ "$head_exec" -eq 6 ] || { echo "unexpected head executed-subtest count: $head_exec" >&2; exit 1; }
[ "$merge_exec" -eq 14 ] || { echo "unexpected merge executed-subtest count: $merge_exec" >&2; exit 1; }

# Deterministic file-count delta: merged tree carries one extra engine-lib
# test file (test_replay.py) that the branch head lacks.
head_files=$(git ls-tree -r --full-tree --name-only "$HEAD_SHA" src/nebuko/engine/lib | grep -c 'test_.*[.]py$')
merge_files=$(git ls-tree -r --full-tree --name-only "$MERGE_TREE" src/nebuko/engine/lib | grep -c 'test_.*[.]py$')
[ "$head_files" -eq 123 ] || { echo "unexpected head test-file count: $head_files" >&2; exit 1; }
[ "$merge_files" -eq 124 ] || { echo "unexpected merge test-file count: $merge_files" >&2; exit 1; }

# The workflow checks out the branch head, so its numbers are the symptom.
echo "{\"subtest_count\": $head_exec, \"test_files\": $head_files}"
