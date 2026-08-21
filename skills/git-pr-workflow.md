# Git / PR Workflow Skill

Procedural playbook for branch management and pull-request mechanics.

## STRICT GIT WORKFLOW RULES

1. NEVER push to main.
2. **Before creating a new branch, check for existing open PRs linked to this issue:**
   - Determine the issue number (from `$TARGET_NUMBER` environment variable or from the GitHub issue context).
   - Run: `gh pr list --search "is:pr #$ISSUE_NUMBER" --json number,headRefName --jq '.[0]'`
   - If a PR already exists → checkout its head branch and push your changes there. Do NOT create a new PR.
   - If no PR exists → proceed with creating a new branch (format: feature/issue-<ID>-<short-name>).
3. **You MUST rebase your branch on `origin/main` at TWO checkpoints:**
   - **Checkpoint 1 (Start of Work):** Immediately upon starting:
     1. Run: `git fetch origin`
     2. Run: `git rebase origin/main`
     3. If conflicts arise, resolve them autonomously before proceeding with any other work.
     4. If the rebase fails and cannot be resolved, stop immediately and report the blocker.
   - **Checkpoint 2 (Before Creating a PR):** Final verification rebase before submitting.
     - `git fetch origin main`
     - `git rebase origin/main`
     - If there are conflicts, resolve them carefully before continuing.
4. After your work is finished, if no existing PR was found for this issue, call `gh pr create` to open a Pull Request. If you pushed to an existing PR's branch, no new PR creation is needed.
5. Do not merge anything yourself. Wait for the review.
6. **All code review discussions MUST happen in the issue context**, not in the PR. When responding to review feedback or providing updates, always post your responses as comments in the issue.
