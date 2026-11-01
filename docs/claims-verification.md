# Claim verification loop

## Overview

The verify-before-commit loop closes the biggest reliability gap in planning:
@@architect plans from model memory, and that memory is unreliable for exactly
the facts architecture decisions depend on — library existence, latest
versions, product capabilities, pricing and limits.

Instead of trusting memory, @@architect attaches its load-bearing external
facts to the hand-off as typed, falsifiable **claims**. The engine validates
them, renders them into @@context's task, and @@context checks each one
independently of the draft plan (the Chain-of-Verification / MARCH pattern:
the checker never sees what it is checking against).

## How to use

@@architect adds claims to its `finalize` hand-off via `AgentCall.verify`:

- `id` — `C1`, `C2`, … unique within the call.
- `claim` — phrased so evidence can confirm **or** refute it.
- `kind` — `support` or `refute`; at least a third of the claims must be
  `refute` (think in opposites: what would you expect to find if the plan were
  wrong?).
- `category` — one of `build_vs_buy`, `version`, `capability`,
  `limits_pricing`, `alternatives`, `compatibility`.
- `if_wrong` — what changes in the plan if the claim is false (min 10 chars).

Claims are only valid on a hand-off to `context`; the engine rejects them on
any other callee and enforces a per-complexity cap (low 3, medium 5, high 8).

## How it works

1. The engine validates the claim list (callee, cap, refute ratio) when the
   `finalize` payload resolves.
2. Valid claims are rendered into @@context's task as a fixed
   `VERIFY THESE CLAIMS` block — never the draft plan, so the check is
   independent.
3. @@context answers per claim with a verdict — `confirmed`, `refuted`,
   `contested` or `inconclusive` — plus evidence (URL, fetch date) and a
   verbatim excerpt, in a `### CLAIM VERIFICATION` table at the top of its
   report, before `### FILES FOUND`.

### Source policy

- **Versions and package existence:** registry APIs only
  (`pypi.org/pypi/<pkg>/json`, `registry.npmjs.org/<pkg>`,
  `gh api repos/<o>/<r>/releases/latest`).
- **Pricing and limits:** the vendor's official page only, with the fetch date.
- **JS-rendered pages** that `curl` cannot read are reported `inconclusive`,
  not guessed.
- **Conflicting sources:** report `contested` and list both sides.
- **Single-source claims** are flagged as single-source.

### Metrics

A per-run claim ledger records each claim and its verdict; the retrospective
skill reads it to compute claims per run, the verdict distribution, and the
share of runs where a refuted claim changed the plan — the evidence for
whether the loop's cost pays off.

## Reconciliation in the plan

The plan artifact must address **every** claim ID in its `## Context` section:
confirmed, refuted (and what changed), or contested/inconclusive.
`## Approaches considered` cites claim IDs, e.g. `✅ C1 refuted → use tenacity`.
Unverified claims go to `## Questions` as explicit assumptions, each with a
default.
