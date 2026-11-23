# Patch: `nebuko-workflow.yaml` dispatch inputs (for a human to apply)

An agent cannot push `.github/workflows/*` (the App token lacks `workflows`).
Apply this to `.github/workflows/nebuko-workflow.yaml` (issue #1447, part of #1099).

Add optional inputs under `on.workflow_call.inputs`. All are optional: the engine
derives the lane without them, so the `lane-binding` guard holds when they are absent.

```yaml
      step:
        description: 'Step to run (client_payload.step). Empty means the first step.'
        required: false
        type: string
        default: ''
      agent_config:
        description: 'Agent config for the step (client_payload.agent_config). Empty means the default.'
        required: false
        type: string
        default: ''
      lane:
        description: 'Override the execution lane (user-repo). Empty: the engine derives it.'
        required: false
        type: string
        default: ''
      credential_owner:
        description: 'Override the credential owner login. Empty: the engine derives it. Never a secret.'
        required: false
        type: string
        default: ''
```

Export the lane pair in the engine step's `env:` (next to `TRIGGER_COMMENT_HEADER`):

```yaml
          WORKFLOW_LANE: ${{ inputs.lane }}
          CLAUDE_CREDENTIAL_OWNER: ${{ inputs.credential_owner }}
```

Do not add `lane` or `credential_owner` to the caller template's `with:`, and never
pass a credential through `with:` (ADR-0008).

Not wired yet: the engine does not consume `step` / `agent_config` from the dispatch.
`Trigger.step` / `Trigger.agent_config` are parsed but do not seed the run yet, so a
dispatch with `step` does not run that step until that follow-up lands. Add the
`step` / `agent_config` inputs and their `env:` export together with that consumer.

Checklist for the live checks (not covered by unit tests):
- On a real `repository_dispatch`, the state records `lane: user-repo` and
  `credential_owner: <actor>`, and `GITHUB_REPOSITORY_OWNER` equals the caller's owner.
- A bot continuation of that state in the org repo is refused with `lane-binding`.
- A fresh `/nebuko run workflow` after a user-repo state does not carry the lane into the next step.
