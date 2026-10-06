# Step 3 / 4 — Tests + fixtures for the agent-config linter

## Instructions

Goal: the rule-contract and CLI-contract tests, plus a per-rule fixture set,
mirroring `tests/engine/test_lint.py` / `tests/engine/test_lint_cli.py` and the
`tests/engine/lint_fixtures/` one-fixture-per-rule pattern.

Fixtures live in `tests/engine/config_lint_fixtures/`. Each is a complete
minimal three-layer config (`providers` → `llms` → `agents`) so that unrelated
rules stay silent:

- `clean.yaml` — fully valid; accompanied by stub files
  (`config_lint_fixtures/.agents/prompts/a.md` and
  `config_lint_fixtures/skills/skill-a.md`) so the prompt/skill checks are
  hermetic.
- `c1_schema.yaml` — two collected schema errors (a wrong-typed `temperature`
  and a missing `prompt_file`), proving all-errors-not-first-fail.
- `c2_dangling_ref.yaml` — two dangling refs in two different agents.
- `c3_unknown_tool.yaml`, `c4_missing_prompt.yaml`, `c5_missing_skill.yaml`.
- `c6_unreferenced.yaml` — an unreferenced `llms` entry and an unreferenced
  `providers` entry (warning-only: must still exit `0`).
- `multi_error.yaml` — combines several rules.
- `url_prompt.yaml` — a URL `prompt_file`, which must *not* be flagged.
- `invalid/malformed.yaml` and `invalid/not_a_mapping.yaml` — the exit-`2` cases.

`tests/engine/test_config_lint.py`:

- contract tests: `RULE_SEVERITY` equals the exact C1–C6 mapping, and
  `dataclasses.fields(LintIssue)` order is
  `[rule, severity, message, step, location, hint]` (same assertions as
  `test_lint.py`).
- one test per rule: lint the matching fixture → expected rule code, severity
  and location; `clean.yaml` → `[]`; `url_prompt.yaml` → no `C4`;
  `multi_error.yaml` covers all present rules; `strict=True` promotes the `C6`
  warning to `"error"`.

`tests/engine/test_config_lint_cli.py`:

- exit `0` on the clean fixture, `1` on an error fixture, `1` with `--strict`
  on the warning-only fixture (and `0` without `--strict`), `2` on a nonexistent
  path and on malformed YAML.
- `--format json` stdout parses as a JSON list whose objects have exactly the
  keys `rule/severity/message/step/location/hint`, with no extra text.
- `run_loop_main(["lint-config", <fixture>])` returns `0`, and the
  `python -m nebuko.engine lint-config` module entry point works.

Verify:

```bash
python -m pytest tests/engine/test_config_lint.py tests/engine/test_config_lint_cli.py
python -m pytest     # full suite, no path argument
```

## Constraints

- Fixtures must be layout-independent: `ConfigLinter` roots file checks at the
  fixture directory, so the shipped `defaults/` lookup must not be required for
  them to pass.
- Do not weaken an assertion to make a test pass; if a rule misbehaves, fix the
  rule.
- The full suite must stay green — no regressions in the workflow-linter tests.

## Expected outcome

- The targeted suite passes in well under a second; the full suite passes with
  no failures.
- Every rule C1–C6 has at least one fixture-driven test asserting code,
  severity and location.

## State on this branch

The tests and fixtures already landed on this branch. Treat this step as
verify-and-complete: run both commands above, fix any gap you find, and commit
only if something changed.

## Relevant files

- `tests/engine/test_lint.py`, `tests/engine/test_lint_cli.py` (the pattern to mirror)
- `tests/engine/config_lint_fixtures/` (fixtures)
- `tests/engine/test_config_lint.py`, `tests/engine/test_config_lint_cli.py`
- `src/nebuko/engine/config_lint.py`, `src/nebuko/engine/config_lint_cli.py`

## Commit message

`[step 3/4] Tests + fixtures for the agent-config linter (issue #774)`
