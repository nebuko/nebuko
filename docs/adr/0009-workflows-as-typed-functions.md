# ADR-0009: Workflows Are Typed Functions — Declared Inputs, Call-Site Binding

**Status:** Accepted — 2026-10-09
**Decider:** olivier-norture
**Related:** #813 (this decision), #756 (declared step outputs / data-flow), #754 (deterministic `tool` steps), #541 (deterministic `script` step), #812/#638 (nested workflow position), ADR-0003 (context management), ADR-0006 (position carry-forward)

## Context

Skills, tools, and MCP are **model-discretionary**: the LLM decides whether to
invoke them. Evals of comparable harnesses show retrieved skills being skipped
in a majority of cases — the agent simply does not call them. A review step that
*needs* the original plan the model never fetched. The original request behind
issue #813 was therefore a **deterministic pre-hook**: guarantee that certain
artifacts (plan, `git diff`, test results) are computed and injected into a
step's context before the model runs, so the model cannot spend turns fetching
them, cannot set up a broken environment, and cannot hallucinate their absence.

The reframing that settles the design: **"mandatory tool call" is a category
error.** A tool call lives in the probabilistic plane by definition; even
OpenAI's `tool_choice: "required"` forces *a* call, provider by provider, and
still permits a junk invocation. Every harness that solves this moves the
guarantee into the **deterministic plane** — Claude Code's `SessionStart` /
`UserPromptSubmit` hooks, Pydantic AI's system reminders at the request tail,
LangChain's `before_model` middleware — and expresses data flow as **named,
declared outputs** (GitHub Actions `needs.<job>.outputs.<name>`, Dify's
`Output` node).

Nebuko already has the deterministic seam — the `script` step executor and the
engine's prompt assembly — but no first-class way to say *what a workflow
needs* and *where the caller supplies it*.

## Decision

**A workflow is a typed function.** It declares **`inputs`** (its parameters)
and **`outputs`** (its return values); steps consume `input.<name>` and
`<step>.<out>` references; the **caller supplies concrete values at the
call site**.

### Signature and binding

- **`WorkflowInputSpec`** declares a parameter: `name`, `kind`
  (`exec` = a deterministic provider command | `str` = a constant), `required`.
- **`InputBinding`** supplies one value at a call site with exactly one source
  key: `exec` (argv list), `str` (constant), or `ref`. A **bare string is a
  `ref` shorthand**, so `diff: input.diff` reads without any flow mapping.
- Plural **`inputs`/`outputs`** at every level — the workflow signature, the
  call-site wiring on a `workflow` step, and the emission on an `agent` step
  (`steps[].outputs`, matching #756's naming).
- **Block-style YAML is canonical**; flow style (`{exec: [...]}`) still parses
  but is undocumented.

### Call semantics (option A — inline call frames)

A sub-workflow call executes **inline in the same run**, not as a new GitHub
run:

1. **Frame push** at the `workflow` call step: bindings are re-checked against
   the callee signature, `str`/`ref` values are frozen **by value**, and an
   `exec` binding is frozen as a **command spec, unmaterialized**.
2. **Just-in-time materialization** at the consuming step's prompt-assembly
   seam: the `exec` command runs *there*, immediately before the model, so
   freshness is depth-invariant.
3. **Lexical consumption**: a callee step resolves `input.<name>` against its
   own frame and `<step>.<out>` against its own step outputs. Caller internals
   never leak in.
4. **Frame pop** at the callee's terminal step: only the declared `outputs`
   cross back into the caller as `<call-step>.<out>`.
5. **Atomic failure**: a required-input, materialization, or callee-step failure
   marks the call step failed — frame discarded, **no partial state bleed** —
   and the caller's `conditions` branch on it.

*Implementation note (v1).* Steps 1–3 and 5 ship: the frame chain is **derived
at resolve time** from the run's carried-forward mount position and decoded at
the engine's input-materialization seam (not a mutable run-loop push/pop
stack), where each binding is frozen and the `MAX_CALL_FRAMES` bound is
enforced. Step 4 — the declared-outputs **return** — is the forward-looking
part: it needs declared step outputs to populate `current_context`, which is
[#756](https://github.com/nebuko/nebuko/issues/756) and is not yet implemented.
Until then `<call-step>.<out>` references resolve only where an output is
already declared, and otherwise degrade to `_(unavailable: …)_`.

### Kind-preserving lazy forwarding

Forwarding a parameter is explicit (`run_test: input.run_test`). Forwarding an
`exec` input forwards the **command binding, not its output**: the ref passes
the frozen command spec down the frame stack and materialization happens
**exactly once, at the deepest consuming step**. A super-parent can therefore
push a command through a middle workflow to a leaf and the artifact is still
fresh at the moment of use. The linter checks each hop's kind independently, so
transitive correctness falls out of per-hop checks.

### Mandatory injection

An `exec` input is a **provider**: the engine — not the model — runs it and
injects the captured stdout as a labeled context block. `required: true`
(default) fails the step **deterministically without invoking the agent**;
`required: false` injects `_(unavailable: <detail>)_` so the prompt shape stays
stable. Either way there is no hallucinated context.

## Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **Step-local `pre:` providers** (an `exec` list attached to the agent step that runs it) | The same guarantee in a worse package: no reuse across workflows, no forwarding, and a second attachment point for the script machinery. The *essential* decision — engine-guaranteed execution with unconditional injection declared in YAML — is identical, so the graph form was chosen to sequence #756 rather than duplicate it. |
| **GHA-native `workflow_call`/`workflow_dispatch` per sub-workflow** (option B) | Forces cross-run state transfer (artifacts/polling), burns GitHub API budget, adds latency, and makes atomic failure propagation genuinely hard. |
| **Flatten callee steps into the caller at resolve time** (option C) | Destroys the function abstraction — no signature, no per-frame scope, no reuse of a `repo:` workflow — and recursion degenerates into name mangling. |
| **Alias/config file** (`setup_dev_env: {exec: nix develop}`) | Deferred as *sugar*: valuable for cross-workflow reuse and constants, but it adds a second config surface and an indirection hop before the core semantics exist. It desugars trivially into call-site binding. |
| **Makefile passthrough** | Rejected as the *mechanism* (it couples orchestration to a build tool and is opaque to the linter). Fine as a *value*: a binding may be `exec: ["make", "test"]`. |
| **Forced tool calls / `tool_choice`** | Probabilistic and provider-brittle; the guarantee must live in the engine. |
| **Implicit auto-pass-through of unresolved inputs** | Spooky action at a distance, unlintable intent, and it breaks the function-signature mental model. Forwarding is explicit. |

## Consequences

- **Determinism is enforceable and lintable.** The workflow linter type-checks
  the graph: every declared input bound exactly once per call site, binding
  kinds matching the declaration (a `ref` is kind-preserving), every reference
  resolvable in scope, and every declared input consumed or forwarded.
- **The engine owns a third context source.** Alongside ADR-0003's recency
  window and pinned comments, materialized step inputs are injected as labeled
  blocks — see the ADR-0003 amendment.
- **`exec` inputs run repo-local commands in the CI job**, the same trust model
  as #541; no new exposure.
- **Nesting is bounded.** The call-frame chain is capped (`MAX_CALL_FRAMES`,
  parity with the resolver's sub-workflow depth guard); an over-deep chain
  fails the step deterministically, before the agent runs.
- **Existing workflows are unchanged**: `inputs`/`outputs` default to empty and
  the resolver emits `STEP_INPUTS` only when a run actually wires something.
- **Open dependency:** `<step>.<out>` consumption and the declared-outputs
  return path depend on #756's step outputs landing in `current_context`.
  Kind-preserving lazy forwarding and `input.<name>` threading are complete
  without it.
