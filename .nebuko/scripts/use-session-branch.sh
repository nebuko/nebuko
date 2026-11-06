#!/bin/sh
# Put a script step on this issue's feature branch, with full history.
#
# Script steps run before the engine prepares the branch, on the workflow's
# own checkout, so without this `HEAD` isn't the code under review. Remove this
# once the engine runs script steps on the session branch.
set -eu

number=$(python3 -c 'import json, os; print(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["issue"]["number"])')
prefix="feature/issue-${number}-"

unshallow=""
if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  unshallow="--unshallow"
fi
git fetch --quiet --no-tags $unshallow origin \
  "+refs/heads/main:refs/remotes/origin/main" \
  "+refs/heads/${prefix}*:refs/remotes/origin/${prefix}*"

branches=$(git for-each-ref --format='%(refname:lstrip=3)' "refs/remotes/origin/${prefix}*")
count=$(printf '%s\n' "$branches" | grep -c . || true)
if [ "$count" -ne 1 ]; then
  echo "expected one ${prefix}* branch, found ${count}" >&2
  exit 1
fi

git checkout --quiet --detach "origin/${branches}"
branch_head=$(git rev-parse HEAD)
echo "branch: ${branches}"
echo "head: ${branch_head}"

# CI tests GitHub's PR merge commit (branch head merged into main), so the
# red-test loop opts into the same shape of tree. Local only: never pushed.
if [ "${NEBUKO_MERGE_MAIN:-}" = "1" ]; then
  main_sha=$(git rev-parse origin/main)
  if git -c user.name=nebuko -c user.email=nebuko@invalid merge \
    --quiet --no-edit --no-ff origin/main; then
    echo "tested: merge of ${branch_head} into ${main_sha}"
  else
    git merge --abort 2>/dev/null || true
    echo "merge: conflict with origin/main; testing branch head"
    echo "tested: branch head ${branch_head}"
  fi
else
  echo "tested: branch head ${branch_head}"
fi
