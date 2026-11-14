# ADR-0011: Command Attempts Are Resolved by the Grammar, Not the Cascade

**Status:** Accepted — 2026-10-10
**Decider:** olivier-norture
**Related:** #867 (this change), #822 (repro), #804 / [ADR-0004](./0004-propagate-human-command-errors-to-thread.md) (error channel), #679 (`clean` alias), #674 (earlier `--from` removal), #644 (`delete comments`)

## Context

Human control of a run is a `/nebuko …` command typed into an issue or
comment. The router resolves a command by parsing it: a well-formed command
short-circuits the routing cascade and runs its semantics; anything the parser
rejects fell through the cascade as if it were an ordinary comment.

That fall-through is the defect. When the thread carries an active workflow
`dispatch` (a pending `review`, `dev`, … hand-off), a mistyped command was
*absorbed* by that dispatch and booted an agent instead of being answered. The
canonical repro (issue #822) is `/nebuko clean comments --from 6095548211`:
`--from` had been dropped from the grammar in issue #674, so the body was
malformed, fell through the `clean` short-circuit, and booted the `review`
agent — the human's intent silently replaced by an unrelated agent run. The
symptom is general: *any* malformed `/nebuko` body is a candidate for
absorption whenever a dispatch is active.

ADR-0004 already made a malformed command speak up (a usage-hint reply), but it
did so *after* the cascade, so it could not prevent the absorption.

## Decision

1. **A `/nebuko` body is an attempt, not prose.** `classify_comment` gains a
   third kind, `"command_attempt"`: a leading `/nebuko` invocation that fails
   the grammar (unknown action, wrong/missing resource, unknown or non-numeric
   flag, missing required flag). The `CommentIntent` carries the recognized
   verb (when the action itself was valid), the per-verb `usage` hint, and the
   machine-stable `detail`. A leading `/` that is not `/nebuko` is **not** an
   attempt and keeps the ADR-0004 fall-through; a bot body is never an attempt.

2. **A command attempt is terminal, before the cascade.** In `resolve_config`,
   a non-bot `command_attempt` returns the ADR-0004 error contract
   (`SKIP_AGENT=true`, `COMMAND_ERROR=true`, `REASON=<usage>`) at the existing
   short-circuit layer, ahead of the workflow-continuation cascade. An active
   dispatch can no longer absorb a mistyped command. The grammar alone decides
   the outcome — valid command runs its semantics, malformed command is
   answered — and the cascade is never consulted for a `/nebuko` body.

3. **`--from <comment-id>` is restored** (superseding the #674 removal) to the
   `delete`/`clean` grammar. Scope: *that comment and every later one* on the
   target issue (`--issue`, defaulting to the invoking issue), forwarded as the
   pre-#674 `DELETE_COMMENTS_FROM` env key. Omitted ⇒ delete all comments
   (today's behaviour). Numeric-only; `--from abc` is a command attempt, never
   a cascade fall-through. The consumer bound is `id >= from`, unvalidated and
   idempotent — a wrong low id is dangerous either way, a wrong high id deletes
   nothing.

## Alternatives considered and rejected

1. **Keep the #674 removal and only fix the fall-through.** Rejected by the
   decider: the CLI framing is *comment = invocation, next comment = the
   answer*, and `--from` is the documented scoped-deletion surface. The fix is
   to accept the command, not merely to reject it more politely.
2. **Order the cascade so commands run first (re-order rather than short-
   circuit).** Rejected: ordering is fragile against future cascade additions;
   a terminal pre-cascade return is an invariant, not an ordering convention.
3. **Existence-validate `--from` against the GitHub API before deleting.**
   Rejected: adds API surface and budget for no safety gain.

## Consequences

- **No agent for any `/nebuko` body.** Valid commands stay terminal (their own
  contract); malformed ones return the ADR-0004 error contract. The #822 class
  of bug — an active dispatch swallowing a command — is structurally closed.
- **#679 safety preserved.** A malformed `clean` is `command_attempt`, never
  `command`, so it can never reach the destructive path; it is answered
  instead.
- **Success is silent.** Unix-`rm` style: deleted comments are the output;
  errors answer with usage. A success echo is a cheap follow-up if wanted.
- **Documentation is true again.** `docs/features.md` and the
  `workflow_command` module docstring regain the scoped-`--from` semantics.
- **Defence in depth retained.** `build_result`'s command-error masking stays
  as a narrow fallback for any parse/classify divergence.
