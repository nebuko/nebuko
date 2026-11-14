# ADR-0015: Human-Actionable Stops Do Not Fail CI

**Status:** Accepted — 2026-10-21
**Decider:** olivier-norture
**Related:** #1057 (this change), #1003 (armed gate hold), #1012 (gate no-op), #804 (command error notice), #793 (soft-kill halt) — **supersedes** [ADR-0004](./0004-propagate-human-command-errors-to-the-thread.md) clause 3 ("exit-code contract unchanged")

## Context

The engine reported a run that stopped *waiting for a human* the same way it
reported a crash: a non-zero process exit. `main()` returned
`int(outcome.exit_code)`, so `ExitCode.HOLD` (10, armed root gate) and
`ExitCode.TERMINAL` (20, mistyped command / failed resolution) both failed the
GitHub Actions job:

```
INFO:nebuko.engine.run_loop:[phase:guards] gate held — human_gate:context held — use /nebuko approve gate or /nebuko reject gate
Error: Process completed with exit code 10.
```

Two problems compounded. The red job sends the maintainer an *error*
notification for a situation that only needs a command; and the reason lived
only in the Actions log, with no link back to the discussion. Some terminal
skips (`force-step: workflow 'x' not found`, an invalid `force-step` name)
posted nothing at all.

The comments in `models.py` and `run_loop.py` claimed exit 10/20 "keeps the host
run green" — that was false for any host that maps a non-zero process status to
a failure, which is every CI host.

## Decision

1. **Outcome codes stay; the process status is derived.**
   `ExitCode` keeps `ADVANCE = 0`, `ERROR = 1`, `HOLD = 10`, `TERMINAL = 20` —
   machine callers and parity tests keep reading the same run-outcome codes.
   `RunOutcome.process_exit_code()` returns `1` only for `ExitCode.ERROR` and
   `0` otherwise, and `main()` returns it. **CI is red only when the engine
   itself broke**, not when it deliberately stopped.
2. **A hold announces itself.** The `HUMAN_GATE_HOLD` branch of the guards
   phase posts `<bot marker>\n@<trigger-author> <reason>` through
   `Engine._post_gate_hold_notice`; the reason already names the next command
   (`use /nebuko approve gate or /nebuko reject gate`). Bot-authored triggers
   stay silent so engine notices never re-enter the chain.
3. **No human-caused stop is silent.** In the terminal-skip branch a
   non-benign, classified skip (a `CONFIG_ERROR`: missing workflow, invalid
   `force-step`, malformed config) and a `COMMAND_ERROR` also post
   `_post_terminal_notice(reason)`. Routine resolver skips (`no_next_agent`,
   `terminal`, an unclassified "nothing to do") stay silent as before.
4. Notices remain best-effort (`log.warning` on a failed post) and pass through
   `_neutralize_agent_triggers`.

## Alternatives considered

1. **Normalize the code in `.github/workflows/nebuko-workflow.yaml`** (map
   10/20 → 0 in the shell, or `continue-on-error`). Rejected for the same reason
   ADR-0004 rejected a CI-level step: the policy would live in CI YAML, would
   have to be duplicated per host, and `continue-on-error` also masks genuine
   crashes.
2. **Collapse `ExitCode` to 0/1.** Rejected: it breaks the `HOLD == 10` /
   `TERMINAL == 20` identity assertions and loses the held-vs-failed signal for
   machine callers.

## Consequences

- A genuine config error (malformed workflow YAML, schema violation) posts its
  reason and exits green. Only an uncaught failure (`ExitCode.ERROR`, a
  traceback) stays red. The thread, not the check colour, is the feedback
  channel for human mistakes.
- The engine's own workflow must not rely on the process status to detect a
  hold; it was not doing so, but any future host integration must read the
  guard verdict rather than the exit code.
- Machine callers keep reading `RunOutcome.exit_code`; the process code is a
  host-facing derivation on top of it.
