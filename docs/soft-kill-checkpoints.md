# Soft-kill checkpoints

## Overview

The soft kill used to be a single event: when the agent's time budget elapsed,
the watchdog interrupted the in-flight call and the wind-down pass asked for a
summary. Everything the agent knew but had not written down by that moment was
lost with the interrupted call.

The single timer is replaced by a schedule of **checkpoints**. Each checkpoint
asks the agent to write or update its session handoff (`.nebuko/handoff.md`)
*before* the budget is gone, so a later kill or retry always has a resumable
record of the run's reasoning.

## How it works

The schedule is computed as fractions of the soft budget (`soft_timeout_s`):

| Checkpoint | When | Delivery |
| -- | -- | -- |
| 1 | 50% of the soft budget | queued message — the in-flight call keeps running |
| 2 | 75% of the soft budget | queued message — the in-flight call keeps running |
| 3 | 100% (the soft kill) | interrupt + wind-down pass (existing behaviour) |

Checkpoints 1 and 2 are **queued**: the engine sends the checkpoint message
from the timer thread while `arun()` keeps running, so the agent receives it at
the next turn boundary and the in-flight call is not cancelled. If a queued
send fails, the engine falls back to the wind-down pattern for that checkpoint
(interrupt, then re-enter a supervised run with the remaining schedule).

Checkpoint 3 is the existing soft kill: the in-flight call is interrupted and
the wind-down pass runs, now asking for a final handoff update instead of a
generic summary. A checkpoint that fires after the run has already finished is
skipped.

## Configuration

- `AGENT_SOFT_TIMEOUT_MINUTES` (default 20) — the soft budget. All checkpoints
  rescale with it: by default they fire at 10, 15 and 20 minutes.
- `WIND_DOWN_TIMEOUT_MINUTES` (default 8) — still bounds the final wind-down
  pass after checkpoint 3.

## What the agent is asked

Each checkpoint message asks for a **decision** (are you converging? narrow
the scope or fold) and for **concrete content** — files touched, commands run,
the next three actions and open questions — written through the `handoff` tool
so the `plan_changes` field records what moved. The messages never give a bare
"X minutes left" or a loop-count estimate: those tend to cause rushing.

The final prompt (checkpoint 3) is a simplified wind-down: stop, call the
handoff tool with the final update, commit and push, then end with the final
message.

## Failure modes

- **No handoff at checkpoint 3** — the harness relies on the previous
  checkpoint's handoff plus the deterministic `ensure_pr` fallback; the
  omission is logged.
- **Soft-retry budget exhausted** (`MAX_SOFT_RETRIES`) — the run halts for
  human resume, unchanged.
- **Hard kill after checkpoint 2** — the checkpoint 2 handoff is already
  committed, so the retry reads it from `.nebuko/handoff.md` as usual.

## Observability

- `handoff writes=<n> plan_changes_total=<n>` is logged per run (drift signal:
  a plan that never moves across checkpoints is a sign of boilerplate writes).
- A checkpoint that produced no handoff update logs a warning.
- The metrics JSON (`output/metrics-{run_id}.json`) gains `checkpoint_count`
  (checkpoints fired for the main run) and `plan_changes_total`.
