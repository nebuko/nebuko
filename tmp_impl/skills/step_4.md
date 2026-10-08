# Step 4: Extend /nebuko command grammar with approve/reject gate actions

Issue #648. Add the dedicated, typed gate commands
`/nebuko approve gate [--feedback "..."]` / `/nebuko reject gate [--feedback "..."]`.

## What to do

In `src/nebuko/engine/lib/resolve_config/sub_domains/workflow_command/workflow_command.py`
(grammar shape: `/nebuko <action> [<resource>] [--flag value]*`):

- `ACTIONS = ("run", "resume", "force-step", "delete", "approve", "reject")`
- `RESOURCE_BY_ACTION`: add `"approve": "gate"`, `"reject": "gate"`
- `FLAGS_BY_ACTION`: add `"approve": {"feedback"}`, `"reject": {"feedback"}`
- `COMMAND_REGISTRY`: add `"approve": "engine"`, `"reject": "engine"`
- Extend the `CommandClass` Literal to include the new class:
  ```python
  CommandClass = Literal["workflow", "local", "engine"]
  ```
  `"engine"` means: handled inline by `resolve_config`, **never dispatched to
  an agent**.

Check the parser's resource/flag validation accepts the new action/resource
pairs; `--feedback` is already a multi-word flag (`_MULTIWORD_FLAGS`, L~96), so
no change is needed there. Confirm `delete`/`clean` behaviour is untouched.

## Relevant file context

- `workflow_command.py`: `WorkflowCommand` dataclass (L~50), `ACTIONS` (L55),
  `RESOURCE_BY_ACTION` (L57), `FLAGS_BY_ACTION` (L64), `CommandClass` (L~75),
  `COMMAND_REGISTRY` (L85), `_REQUIRED_FLAGS` (L~90), `_GRAMMAR_ALIASES` (L~105),
  `CommandGrammar` (L~110+). `__all__` at the top lists the exported names.
- Co-located parser tests:
  `src/nebuko/engine/lib/resolve_config/sub_domains/workflow_command/test_workflow_command.py`.

## Tests

- `/nebuko approve gate --feedback "fix the X section"` parses to
  `action="approve"`, `resource="gate"`, `params["feedback"] == "fix the X section"`.
- `/nebuko reject gate` parses with empty feedback.
- Malformed variants (missing resource, unknown action, unknown flag) follow
  the existing parser error/`None` behaviour.

## Expected outcome

- `python -m pytest src/nebuko/engine/lib/resolve_config/sub_domains/workflow_command/ -q`
  is green.
- `COMMAND_REGISTRY["approve"] == "engine"` and `COMMAND_REGISTRY["reject"] == "engine"`.

## Constraints

- Do not reorder or rename existing actions.
- Keep the change inside `workflow_command.py` and its co-located tests.
