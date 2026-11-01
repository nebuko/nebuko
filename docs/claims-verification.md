# Claim Verification (verify-before-commit)

## Overview

@@architect plans from model memory with no tools. When a plan depends on an
external fact — does a library already cover this? what is the latest version?
what does a service's free tier allow? — that memory is unreliable.

The claim-verification loop makes those dependencies explicit and falsifiable.
While analyzing, @@architect writes its assumptions as **verification claims**
and sends them to @@context on the typed hand-off. @@context checks each claim
against real evidence and returns a per-claim verdict, so the plan is
reconciled against evidence *before* it reaches the human gate.

Independence is structural: the engine hands @@context only the rendered claim
block, never the draft plan — the checker cannot see what it is supposed to
confirm, so evidence can refute as easily as confirm.

## How to use

A claim is a `VerificationClaim` on `finalize(turn_outcome.agent_call.verify)`:

| Field | Meaning |
|---|---|
| `id` | `C1`, `C2`, … — unique within the call; cited in the plan and report |
| `claim` | Phrased so evidence can confirm **or** refute it ("tenacity ≥ 9.0 supports async retry", not "check tenacity") |
| `kind` | `support` (evidence that would confirm) or `refute` (evidence that would prove the assumption wrong) |
| `category` | one of `build_vs_buy`, `version`, `capability`, `limits_pricing`, `alternatives`, `compatibility` |
| `if_wrong` | What changes in the plan if the claim is false (≥10 chars). Claims whose `if_wrong` would not change the plan are dropped. |

Example:

```json
{"id": "C1", "kind": "refute", "category": "build_vs_buy",
 "claim": "No maintained Python library provides exponential-backoff retry for async callables",
 "if_wrong": "Drop the hand-rolled retry helper and depend on tenacity"}
```

Rules the engine enforces:

- Claims are only valid when the callee is @@context (`agent_name == "context"`); any other callee is rejected.
- The number of claims is capped by complexity tier: **low 3, medium 5, high 8**.
- At least **one third** of the claims must be `kind="refute"` — think in opposites, don't only seek confirmation.

Send them on the same-turn hand-off:

```
finalize(
  report="…",
  turn_outcome={
    "appreciation": "approved",
    "agent_call": {"agent_name": "context", "task": "…",
                   "verify": [ {"id": "C1", …}, … ]},
  },
)
```

## How it works

- **Validation.** On a finalize `agent_call`, the engine validates the claims: callee rule, per-tier cap, refute ratio, unique IDs, non-trivial `if_wrong`. A failed validation rejects the finalize payload with the error list, which the caller sees as a tool error.
- **Rendering.** Valid claims are appended to the callee's task as a fixed block:

  ```
  VERIFY THESE CLAIMS (independently; report verdict per claim)
  C1 [refute/build_vs_buy] No maintained Python library provides … — if wrong: Drop the hand-rolled retry helper …
  ```

  Only the claims are rendered. The draft plan never enters the context task, so the check is independent of the hypothesis (Chain-of-Verification / MARCH).
- **Verdicts.** @@context answers with a `### CLAIM VERIFICATION` table at the top of its report: `ID | Verdict | Evidence (URL, fetched date) | Verbatim excerpt`. A verdict is exactly one of `confirmed`, `refuted`, `contested`, `inconclusive`.
- **Reconciliation.** The plan artifact's `## Context` must address **every** claim ID (confirmed, refuted — and what changed —, or contested/inconclusive). `## Approaches considered` cites claim IDs (e.g. `✅ C1 refuted → use tenacity`). Unverified claims go to `## Questions` as explicit assumptions with a default. Claim IDs carry into the implementation steps so review can trace each dependency choice back to its evidence.

## Source policy

@@context verifies claims against evidence under these rules:

- **Versions and package existence:** registry APIs only — `pypi.org/pypi/<pkg>/json`, `registry.npmjs.org/<pkg>`, `gh api repos/<o>/<r>/releases/latest`.
- **Every package the plan names:** check through the registry that it exists, and record the exact name, maintainer and last release.
- **Pricing and limits:** the vendor's official page only, with the fetch date. Pages rendered by JavaScript that `curl` cannot read are reported as `inconclusive`, not guessed.
- **Conflicting sources:** report `contested` and list both sides.
- **Single source:** flag the claim as single-source.

The forbidden list is unchanged in spirit: a **verdict on a factual claim is allowed**; recommendations (root cause, fix, "use X instead") still are not.

## Metrics

`ClaimLedger` records, per run: the claims sent, their verdicts, and whether a
`refuted` claim changed the plan. `summarize()` aggregates:

- claims per run,
- verdict distribution (`confirmed`/`refuted`/`contested`/`inconclusive`),
- the share of runs where a refuted claim changed the plan.

The retrospective skill reads this to judge whether the verification cost pays
off. A refuted claim does **not** force a second planning pass beyond the
existing one-time re-ask of @@context: it is reconciled in `## Context`, and a
high-impact refutation may consume that re-ask.

## Cost model

| Tier | Cap | Min refute |
|---|---|---|
| low | 3 | 1 |
| medium | 5 | 2 |
| high | 8 | 3 |
