# Step 1: Grammar — add `delete comments` action to the /workflow tables + parser tests

> Status note: this step was already implemented and committed in a previous
> dev run as `[step 1/4] Grammar: add delete comments action to /workflow
> tables + parser tests`. If the branch already carries it, verify the
> outcome below and confirm the tests pass instead of redoing the work.

## Instructions

Extend the /workflow command grammar for the new human-only command `/workflow delete comments --issue <n> [--from <comment-id>]`. Files: `.github/scripts/resolve_config/sub_domains/workflow_command/workflow_command.py` — add `"delete"` to the `ACTIONS` tuple; add `RESOURCE_BY_ACTION["delete"] = "comments"`; add `FLAGS_BY_ACTION["delete"] = {"issue", "from"}`. Both flags are OPTIONAL: `_parse_action_parts` must succeed with zero, one, or two flags for `delete`, and reject any flag not in the set. The parser must validate that any provided `--issue` / `--from` value is numeric (digits only); non-numeric → return None (command treated as absent — existing unknown-command behavior). `WorkflowCommand` dataclass gains NO fields — the action flows through `action`/`resource`/`params`. Do not touch the existing `run`/`resume`/`force-step` entries. In `.github/scripts/resolve_config/sub_domains/workflow_command/test_workflow_command.py`, extend (do not rewrite) the table-pinning tests: update `test_actions`, `test_resource_by_action`, `test_flags_by_action` expected values to include the new entries. Add parser test cases: (a) `/workflow delete comments` (no flags) → action=delete, resource=comments, params={}; (b) `/workflow delete comments --issue 123` → params={"issue": "123"}; (c) `/workflow delete comments --issue 123 --from 5811583860` → params with both; (d) `--issue abc` (non-numeric) → None; (e) `--from abc` → None; (f) `/workflow delete issues` (wrong resource) → None; (g) `/workflow delete comments --all` (unknown flag) → None. Run the workflow_command test suite; all existing + new tests must pass.

## Expected outcome

- `ACTIONS` contains `"delete"`; `RESOURCE_BY_ACTION["delete"] == "comments"`; `FLAGS_BY_ACTION["delete"] == {"issue", "from"}`.
- Parser accepts 0/1/2 flags for `delete`, rejects non-numeric values, wrong resource, and unknown flags (returns None).
- All existing + new workflow_command tests pass.

## Relevant files

- `.github/scripts/resolve_config/sub_domains/workflow_command/workflow_command.py` — grammar tables + parser (the `delete` entry is additive; do not touch `run`/`resume`/`force-step`).
- `.github/scripts/resolve_config/sub_domains/workflow_command/test_workflow_command.py` — table-pinning + parser test corpus to extend.

## Verification

Run the workflow_command test suite (pytest against `test_workflow_command.py`); all tests must pass.