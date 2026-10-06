# Step 4 / 4 — CI workflow + docs for the agent-config linter

## Instructions

Goal: gate the linter in CI and document it, mirroring how the workflow linter
is wired and documented.

1. `.github/workflows/lint-agent-config.yaml`, structurally copied from
   `.github/workflows/lint-workflows.yaml`: `name: Lint agent config`;
   `on: pull_request: paths:` covering the shipped agent configs
   (`defaults/agents/**`), the linter sources
   (`src/nebuko/engine/config_lint.py`,
   `src/nebuko/engine/config_lint_cli.py`) and the workflow file itself;
   `permissions: contents: read`; the same job steps
   (`actions/checkout@v5`, `actions/setup-python@v6` with
   `python-version: "3.13"`, `pip install pyyaml pydantic`), with the lint step
   running the linter against the shipped configs under `PYTHONPATH: src`:

   ```yaml
   - name: Lint agent-config YAML
     env:
       PYTHONPATH: src
     run: python -m nebuko.engine lint-config defaults/agents/
   ```

   Non-strict on purpose: the shipped configs intentionally carry `C6` warnings
   (staged models), so the job must exit `0`.

2. Extend `docs/lint.md` with a section documenting the agent-config linter,
   kept stylistically consistent with the existing workflow-linter section:
   rationale (mirror of the workflow linter, issue #774), usage examples, the
   `0`/`1`/`2` exit-code table, the `--strict` and `--format` flags, and a rule
   reference table for C1–C6 (code, severity, what it catches, a bad snippet and
   its output). Note that tool names are enforced against the canonical set in
   `src/nebuko/engine/lib/agent_script/features/run_agent/run_agent.py`.

Verify:

```bash
PYTHONPATH=src python -m nebuko.engine lint-config defaults/agents/   # exit 0
python -m pytest                                                       # full suite
```

## Constraints

- The acceptance gate is exit `0` on the committed configs. Any genuine C1–C5
  finding in a committed config is a real defect: fix the config or report the
  blocker — do not silence the rule.
- Any example output in the docs must be a real transcription of what the
  command prints.
- Do not modify `.github/workflows/lint-workflows.yaml`.

## Expected outcome

- CI fails on a config error and passes on the shipped configs.
- `docs/lint.md` lets a reader run the command and interpret every rule and
  exit code without reading the source.
- The full suite still passes.

## State on this branch

The CI workflow and the docs section already landed on this branch. Treat this
step as verify-and-complete: run both commands above, fix any gap you find, and
commit only if something changed.

## Relevant files

- `.github/workflows/lint-workflows.yaml` (the model)
- `.github/workflows/lint-agent-config.yaml` (new)
- `docs/lint.md` (workflow-linter section; append the agent-config section)
- `defaults/agents/agent-config*.yaml` (the configs CI lints)
- `src/nebuko/engine/config_lint.py`, `src/nebuko/engine/config_lint_cli.py`

## Commit message

`[step 4/4] CI workflow + docs for the agent-config linter (issue #774)`
