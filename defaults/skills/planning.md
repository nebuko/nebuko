---
description: "Emit the single planning artifact: root cause, approaches, chosen design, implementation steps."
---

# Planning Skill

Be concise: a human reads the artifact at a gate and @@dev materializes it, so write what is needed to decide and to implement, nothing more.

## The Planning Artifact (single turn)

**Claim verification (issue #1250).** The claims @@architect attached to the context hand-off (`C1`, `C2`, …) are the run's verified-evidence ledger:
- Claims still open and material go through the one-time re-ask of @@context via `verify`.
- **`## Context` must address every claim ID** — confirmed, refuted (and what changed in the plan as a result), or contested/inconclusive. Ignoring late or contradicting evidence is the failure mode this guards against.
- `## Approaches considered` cites claim IDs, e.g. `✅ C1 refuted → use tenacity`.
- Unverified claims go to `## Questions` as explicit assumptions, each with the default you proceed with.
- Claim IDs carry into the implementation steps, so review can trace each dependency choice back to its evidence.

**Evidence discipline.** @@context tags each finding `measured`, `read` or `inferred`. Use the tags as follows:
- **No unmeasured numbers.** A timing, size, saving or performance figure in the plan must come from a `measured` finding. Any other estimate goes to `## Questions` as an assumption, never into `## Decision`. Write "unmeasured" rather than "~1–2s".
- **`inferred` is a lead, not a fact.** Do not base a decision on it. Re-ask @@context to measure it, or list it under `## Questions`.
- **Trace each fix to the outcome the user asked for.** For every change in `## Decision`, say which observable it moves (a log line, a timestamp, a test) and whether the user's real outcome moves with it. A fix that only moves a metric, or shifts the same cost later in the run, is not a fix.
- **Check the fix, not just the diagnosis.** When a fix's effect can be measured cheaply before implementation (e.g. "does compiling X remove the cost?"), have @@context measure it in the one-time re-ask instead of assuming it.
- **Read `### SURPRISES` and `### NOT VERIFIED`.** Address each relevant surprise in `## Context`. Carry each unverified item into `## Questions`.

**Context missing or insufficient.** Ask @@context for more at most **once** per run, listing exactly the facts missing.
Past that, or when the gap is minor, write the plan anyway and record each assumption under `## Questions` with the default you proceed with.
Never invent file paths, class names or fields the context did not confirm. Nobody answers mid-run, so never stop with a "blocked" post and no plan.

Sections, **in this order**:

1. **`## Context (root cause)`** (≤5 lines). What is wrong or missing, and the verified facts that constrain the fix.
2. **`## Decision`.** The chosen design, affected subsystems/interfaces and key rulings. 
3. **`## Approaches considered`.** Compact table (Approach / Pros / Cons / Verdict, one line per cell), chosen one marked ✅. Include the real alternatives, normally 2–3; if fewer than 2 genuinely exist, say so in one line. Never invent a straw man.
4. **`## Object Design Contract`.** For every new/changed class: file path, class name, fields with types, and method signatures each marked `# @stub-protected`. The stub step materializes this 1:1; deviations require a new plan. Omit only if there is no class, and say why in one line.
5. **`## Implementation Plan`** (collapsed). Ordered dev steps, one line each (file — what and why):
   ```
   <details>
   <summary>Implementation Plan</summary>

   1. **`path/to/file`** — what changes and why
   </details>
   ```
6. **`## Coverage`** (required when the analyze step emitted a requirements ledger). One row per ledger ID — ID / Covered by (the decision or implementation step that covers it) / Verified by (the test name or check):
   ```
   | ID | Covered by | Verified by |
   |---|---|---|
   | R1 | Decision §2, step 3 | test_r1_... |
   ```
   Every `R` and `K` must appear. A missing ID makes the plan invalid unless the row is explicitly `deferred: <reason>` (use it only for work genuinely out of scope). Each NG entry is checked as "not violated"; Q entries rank the options in `## Approaches considered`.
7. **`## Questions (if relevant)`** (optional). Open questions and assumptions, each with the default you proceed with. Omit when none.

Signatures and field lists are allowed; method *bodies* and implementation code are forbidden. String/namespace encodings or function-only designs are a last resort: justify in one sentence why no class can model it.
