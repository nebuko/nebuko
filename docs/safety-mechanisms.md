---
title: "Safety mechanisms"
audience: operator
covers:
  - src/nebuko/engine/lib/circuit_breaker.py
  - src/nebuko/engine/lib/loop_guard.py
sidebar_group: "Concepts"
sidebar_label: "Safety mechanisms"
sidebar_order: 22
---

# Safety mechanisms

Three independent guards prevent runaway automation. All live in
`engine/lib/` and run *before* an agent executes.

## Circuit Breaker — `circuit_breaker.py`

Counts `<!-- bot-agent-{name} -->` markers across recent issue comments (the
universal bot marker itself is excluded). When the count reaches the threshold
(input `circuit_breaker_threshold`, operative default **25**), the run is
skipped and a warning comment is posted asking a human to review the issue.
Output: `CIRCUIT_BREAKER_TRIPPED=true/false`. A human can raise (or lower) the
budget for a single issue with `/nebuko update circuit-breaker --value <n>`
(issue #839); the override wins over the workflow parameter.

## Loop Guard — `loop_guard.py`

Two checks:

1. **Same-agent loop** — if the triggering bot comment was posted by the very
   agent about to be invoked, skip. Cross-agent calls remain allowed
   (`architect → context` is fine).
2. **Human + agent ping** — if a human is pinged alongside an agent, skip, so
   the human (not the machine) orchestrates the next move.

Output: `SKIP_AGENT=true/false`.

GitHub REST rate limits that constrain these comment-fetching guards are
documented in [github-api-limits.md](github-api-limits.md).
