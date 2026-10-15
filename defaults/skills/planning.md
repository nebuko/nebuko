---
description: "Emit the single planning artifact: root cause, approaches, chosen design, implementation steps."
---

# Planning Skill

Be concise: a human reads the artifact at a gate and @@dev materializes it, so write what is needed to decide and to implement, nothing more.

## The Planning Artifact (single turn)

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
6. **`## Questions (if relevant)`** (optional). Open questions and assumptions, each with the default you proceed with. Omit when none.

Signatures and field lists are allowed; method *bodies* and implementation code are forbidden. String/namespace encodings or function-only designs are a last resort: justify in one sentence why no class can model it.
