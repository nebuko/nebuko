# Step 8: Wire CI identity env vars and gate-token allow-list

Issue #648. Gate authority is anchored to the **GitHub event author** supplied
by CI, never to comment body markers.

## What to do

In `.github/workflows/nebuko-workflow.yaml`:

1. Pass the trigger author identity into the engine environment (the
   `run-task` job `env:` block ~L127–131):
   ```yaml
   TRIGGER_AUTHOR_LOGIN: ${{ github.event.comment.user.login || github.event.issue.user.login }}
   TRIGGER_AUTHOR_TYPE: ${{ github.event.comment.user.type || github.event.issue.user.type }}
   TRIGGER_AUTHOR_ASSOCIATION: ${{ github.event.comment.author_association || github.event.issue.author_association }}
   ```
   (Verify the expression fallbacks behave as intended for both
   `issue_comment` and `issues` events.)

2. Extend the `issue_comment` `if:` allow-list (L~105–126) so a comment
   containing `/nebuko approve gate` or `/nebuko reject gate` reaches the
   workflow **only when** the comment author's `author_association` is one of
   `OWNER`/`MEMBER`/`COLLABORATOR` — **always-on regardless of `CALLER_AUTH`**
   (Policy A: gate commands are privileged even when general triggering is
   open). Today `CALLER_AUTH: off` lets any commenter through, so the gate
   tokens need their own privileged branch in the `if:` condition.

3. Keep the existing bot-marker (`<!-- bot-agent-`, `<!-- bot-workflow-terminal -->`)
   and machine-token (`<!-- bot-trigger:`, `<!-- bot-subtask-complete`,
   `<!-- workflow-continue -->`, `"function_call"`) exclusions intact.

4. Engine side: `resolve_config` must build `AuthorIdentity` from these env
   vars (`os.environ`), never from body markers (step 3 factory).

## Relevant file context

- `.github/workflows/nebuko-workflow.yaml`: `CALLER_AUTH` input (L51–55);
  `run-task` `if:` (L82–126); job `env:` (L~127–131).
- `.github/workflows/comment-flow.yaml` (L14–20) calls the reusable workflow
  with `CALLER_AUTH` defaulting to `'off'` — so the gate-token privilege branch
  must not depend on `CALLER_AUTH`.
- GH SOTA precedent: gh-aw field guide on `issue_comment` slash commands /
  permission squatting; `xt0rted/slash-command-action` `permission-level`.

## Tests

- CI YAML is not unit-tested in-repo; validate by YAML parse
  (`python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/nebuko-workflow.yaml').read_text())"`)
  and by inspection against the two gate tokens.
- Engine-side identity-from-env is covered by step 3 tests — add a
  `resolve_config`-level test asserting a bot/machine body with
  `/nebuko approve gate` is inert.

## Expected outcome

- YAML parses; gate tokens are gated on privileged association regardless of
  `CALLER_AUTH`; env vars are exported to the engine.

## Constraints

- Do not weaken the existing bot/machine exclusions.
- Do not add new CI jobs/steps beyond wiring the env + condition.
