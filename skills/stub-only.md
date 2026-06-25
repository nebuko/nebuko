# Stub-Only Mode

You are in stub-implementation mode. Follow these constraints strictly.

## What You MAY Do

- Add code ONLY inside existing function stubs (bodies of functions with signatures already defined)
- Create helper/sub-functions that are called exclusively from within the stubbed functions
- Update data class fields if the genius plan explicitly lists new fields
- Write test cases matching the spec scenarios (tests are expected to fail against stubs)

## What You MUST NOT Do

- Modify function signatures (parameter list, return type, name)
- Modify data class structure (add/remove classes, change inheritance)
- Create new top-level modules or packages
- Introduce new public APIs
- Implement business logic beyond filling in stub bodies

## Escalation Protocol

If you determine that the implementation requires:

- A new function at the module level
- A change to an existing signature
- A new data class or enum
- Any architectural decision beyond filling in stub bodies

→ Stop immediately and invoke @@genius with a clear explanation of what is needed.
Do NOT proceed with unauthorized changes.

## Protected Signatures

The architect (genius) will mark protected signatures with `# @stub-protected` comments. These must never be modified:

```python
# @stub-protected: signature defined by architect — do not modify
def process_password_reset(email: str) -> PasswordResetResult:
    ...
```
