# Step 3: Add AuthorIdentity model and machine-comment detector

Issue #648. Gate release authority must be anchored to the **GitHub event
author**, never to body content.

## What to do

### 3a. New package `author_identity`

`src/nebuko/engine/lib/resolve_config/sub_domains/author_identity/author_identity.py`
(+ `__init__.py`), mirroring `sub_domains/workflow_command/`:

```python
class AuthorIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    login: str
    is_bot: bool
    association: str = ""
    is_machine_comment: bool = False

    def may_decide_gate(self, allowlist: tuple[str, ...] = ("OWNER", "MEMBER", "COLLABORATOR")) -> bool:  # @stub-protected
        """True iff not is_bot AND not is_machine_comment AND association in allowlist."""


def build_author_identity(login: str, user_type: str, association: str, trigger_body: str) -> AuthorIdentity:  # @stub-protected
    """Factory used by resolve_config — identity comes from event env, never from body markers."""
```

Rules for `build_author_identity`:
- `is_bot = (user_type == "Bot") or (login in BOT_LOGIN_DENYLIST)` where
  `BOT_LOGIN_DENYLIST` is a module-level tuple of known machine logins
  (machine users can be typed `User` — GitHub community discussion #65546).
- `is_machine_comment = is_machine_comment_in(trigger_body)` from parse_trigger
  (see 3b).

### 3b. Machine-token detector in parse_trigger

In `src/nebuko/engine/lib/resolve_config/features/parse_trigger/parse_trigger.py`
add:

```python
MACHINE_TOKENS: tuple[str, ...] = (
    "<!-- bot-workflow-terminal -->",
    "<!-- bot-agent-",
    "<!-- bot-trigger",
    "<!-- bot-subtask-complete",
    "<!-- bot-summarizer-trigger -->",
    "AGENT_RUN_CONFIG",
)

def is_machine_comment(trigger_body: str) -> bool:  # @stub-protected
    """True when any machine token appears anywhere in the body."""
```

Add both to `__all__`.

## Relevant file context

- `src/nebuko/engine/lib/resolve_config/features/parse_trigger/parse_trigger.py`:
  existing bot detection `UNIVERSAL_BOT_MARKER` (line 40), `has_universal_bot_marker`
  (L43), `is_bot_comment` (L56); machine-token regexes `_SUBTASK_COMPLETE_RE`
  etc. (L176–204).
- Existing sub-domain pattern: `src/nebuko/engine/lib/resolve_config/sub_domains/workflow_command/`.
- Avoid a circular import: `parse_trigger` imports from
  `sub_domains.workflow_command` but not from `author_identity`, so
  `author_identity` importing `is_machine_comment` from
  `features.parse_trigger.parse_trigger` is safe. Verify at import time.

## Tests (critical invariant)

- A comment **authored by the bot account** containing `/nebuko approve gate`
  is inert: `may_decide_gate()` is `False`.
- A privileged human with a plain gate command: `True`.
- A human comment that echoes any machine token: `False`.
- `association` of `NONE` / `CONTRIBUTOR`: `False`.
- A deny-listed login with `user_type == "User"`: `False` (is_bot True).
- `user_type == "Bot"`: `False`.

## Expected outcome

- `python -m pytest src/nebuko/engine/lib/resolve_config/sub_domains/author_identity/ -q`
  and the parse_trigger tests are green.

## Constraints

- Do not change existing signatures in `parse_trigger.py`; only add.
- Keep `BOT_LOGIN_DENYLIST` module-level and documented (the GH #65546 caveat).
