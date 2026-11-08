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
echo "branch: ${branches}"
echo "head: $(git rev-parse HEAD)"
