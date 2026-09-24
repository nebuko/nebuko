# ADR-0002: Agent Timeout Budgets — 30-min Hard Stop, 20-min Soft Kill

**Status:** Accepted — 2026-09-23
**Decider:** olivier-norture
**Related:** #639 (this discussion), #489 (graceful interruption pipeline), #626 (1h hang), #601 (token/price budgets), #636 (sub-workflow steps)

## 1. Context

The dev agent sometimes needs long execution steps. The hard timeout was originally set to 2h to be safe, but a hung run then burns the full budget with zero mid-run visibility (failure mode observed in #626). The discussion in #639 weighed:

- lowering the budgets (1h? 30min?),
- an in-run state-check prompt injected periodically to force the agent to self-report,
- soft-kill + fresh-session restart,
- whether sub-workflows would make long steps unnecessary.

Two relevant facts from the SDK research (openhands-sdk v1.49.4):

- The SDK provides rule-based stuck/loop detection (on by default), an iteration cap (500) and a *cost* budget — but **no time-based budget and no periodic state-check**; time control remained entirely on the repo's watchdog.
- The repo already ships (from #489) a complete graceful-interruption pipeline: soft watchdog → `conversation.interrupt()` → `WIND_DOWN_PROMPT` (commit & push, open/update PR, hand-off summary with completed/current/blocking/remaining) → deterministic `ensure_pr()` fallback → recovery-log capture injected into the next run's context.

## 2. Decision

**v1 — short budgets, reuse the existing graceful pipeline:**

- GHA hard stop: `timeout-minutes: 30` on the "Run OpenHands Agent" step — a pure backstop only.
- Soft kill: `AGENT_SOFT_TIMEOUT_MINUTES = 20` — the watchdog interrupts the in-flight LLM call at 20 min.
- Wind-down: `WIND_DOWN_TIMEOUT_MINUTES = 10` — reserved for the agent to stop new work, commit & push, open/update the PR, and write the hand-off summary.

Every 30-minute cycle that hits the soft kill is a **forced thinking point**: the agent must pause, checkpoint, and prepare for continuation. The next run resumes from a **fresh session** via the existing recovery-log pipeline (tool-call trace + posted hand-off summary in history), which avoids the "lost in deep exploration" context-bloat failure mode. Worst-case work is never lost: the deterministic `ensure_pr()` fallback guarantees a WIP checkpoint even if the LLM wind-down is uncooperative.

All knobs remain env-overridable (`AGENT_SOFT_TIMEOUT_MINUTES`, `WIND_DOWN_TIMEOUT_MINUTES`); the 20/10 values are set explicitly on the agent step in `.github/workflows/openhands-multi-agent.yml` and are the operating defaults for this repo. The library-level fallback in `timeout_budget.py` stays at 110/8 as a generic safety net for other deployments.

## 3. Alternatives considered and rejected

1. **Just lower to 60/90 min** — still blind for most of the run; doesn't create the pause/think/resume rhythm; still leaves long undisturbed stretches.
2. **In-run state-check heartbeat (v2 candidate)** — `CheckpointPolicy` + `STATE_CHECK_PROMPT`: interrupt every 30 min, bounded pass asking the agent where it is (exploring/implementing/testing/stuck), commit checkpoint, self-justify continuation or self-soft-kill; resume the *same* conversation. Strictly better observability, but more moving parts (orchestrator becomes a loop of bounded passes). **Deferred to v2** — to be adopted only if short-cycle restarts prove insufficient.
3. **Bounded fresh-session auto-restart loop (v2 candidate)** — `RestartPolicy` + `MAX_TIMEOUT_RESTARTS`: on graceful timeout, auto re-run the same step (dispatch `loop`) up to N times, then `hold` for human review. **Deferred to v2** — it is the natural follow-on to this ADR's decision (which currently relies on the human/workflow re-pinging the step).
4. **Rely on SDK primitives only** — stuck detection / iteration cap / cost budget exist, but there is no wall-clock budget in the SDK (verified in v1.49.4); the watchdog is still required. Making `stuck_detection=True` and `max_iteration_per_run=500` explicit is a candidate v2 hygiene item (defaults currently apply implicitly).
5. **Wait for sub-workflows to make steps small enough** — `sub_impl` is still broken (#636); even when it works, sub-workflows bound logical step size but not the clock on a runaway step. Complementary, not a replacement.

## 4. Consequences

- Steps longer than ~20 min now span multiple runs: each continuation starts fresh from the WIP commit + recovery log. No work is lost; some re-doing/drift between cycles is possible (mitigation: hand-off summary is contractually required).
- Per-cycle overhead: up to 10 min wind-down + fresh-session setup.
- The 15-agent-marker circuit breaker remains the second line of defense against runaway issues.
- **Open item:** 20 + 10 = 30 leaves no slack for GHA step setup/teardown. If the hard stop is observed firing *during* wind-down, raise `timeout-minutes` to ~35 (no other changes needed).

## 5. Future work (explicit non-goals of v1)

- v2: in-run monitoring that decides kill-or-continue (heartbeat or activity-based), bounded auto-restart loop, explicit SDK execution-control wiring, first-class `STUCK` outcome with recovery-log capture, token/price soft-kill (#601), per-role budgets.