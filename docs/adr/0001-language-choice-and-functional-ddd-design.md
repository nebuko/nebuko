# ADR-0001: Language Choice (Python 3.13) and Functional DDD Package-by-Feature Design

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Olivier Norture (author), @genius (architecture)
**Scope:** `gitflow-ai` open-source + commercial (Apache-2.0) refactoring baseline
**Records the thinking from:** issue #627

## 1. Context

`gitflow-ai` is currently a 100% Python codebase: 161 `.py` files, ~30,164 LOC, 55 test files, and **no packaging metadata** (no `pyproject.toml`/`setup.py`/`requirements.txt`).

Relevant facts that constrain the decisions:

- **OpenHands is a hard runtime dependency on the Python SDK** (`openhands.sdk`). 14 files import it, and the project **extends the SDK at the type level** (a custom tool subclasses `openhands.sdk.tool.tool`). This is extension, not just a call.
- **GitHub/Git integration is `subprocess` around the `gh` CLI** — language-independent, costs nothing in any choice.
- **A declarative YAML layer** (9 `workflows/*.yaml` + `agent-config.yaml` + `workflow.yaml`) sits above the code and is language-independent — it survives any choice.
- **The codebase is already class-light and function-heavy** (292 `class` tokens, 36 dataclass usages), and `docs/architecture_code.md` already documents the "functional + DDD package-by-feature" style as the *existing* convention.
- OpenHands **MIT-licensed** (Apache-2.0 compatible); the repo already carries an Apache-2.0 `LICENSE` at root.

Three decisions must be locked and documented so the refactor — and any AI agent performing it — has a stable target: **(1)** implementation language, **(2)** package/organization model, **(3)** the precise meaning of the "functional programming" phrase used across the docs.

## 2. Decision

### 2.1 Language — stay in Python 3.13; do **not** migrate

We keep Python. No migration to TypeScript, Go, or Rust.

Rationale, decisive factor first:

1. **The OpenHands boundary is the load-bearing constraint.** OpenHands ships a mature Python SDK with type-level tool extension. Its TypeScript client is explicitly documented as *"intended for early testing and development purposes only… expect frequent updates,"* and no first-class Go/Rust SDK exists (REST/WebSocket only). A migration would push the **custom-tool capability — the one place this project *extends* the SDK, not merely calls it — onto an immature or non-existent surface.** That is a capability loss, not a stylistic change.
2. **Migration cost is total; benefit is marginal.** A rewrite rewrites ~30k LOC + 55 tests + the `.agents/prompts/*.md` (10 files) and `skills/*.md` (7 files) guidance written against the Python shape. The only real non-Python benefit — single-binary distribution — does not apply to a CI-embedded engine.
3. **The codebase is already in the target style.** We are consolidating a style we already use, not fighting one.
4. **AI-agent searchability.** The stated goal ("easy code search for AI agents") is best served by keeping the existing layout, existing markdown guidance, and existing declarative YAML layer — all of which survive a Python refactor and are invalidated by a language rewrite.

Safety/correctness parity is bought with **tooling, not language**: `mypy --strict` (or `pyright --strict`) + frozen dataclasses recovers most of what Go/Rust give for free in this domain.

*Alternatives considered:* **TypeScript** (rejected — immature client, REST-only custom tools, full rewrite for no workload gain); **Go / Rust** (rejected — no SDK → REST-only, single-binary benefit not required, high velocity cost).

### 2.2 Organization — DDD package-by-feature (one domain = one folder)

- A real package rooted at `src/gitflow_ai/`, **one folder per domain**:
  ```
  src/gitflow_ai/
    agent_script/        # OpenHands-facing orchestration domain
    resolve_config/
    workflow_state/
    config/
    features/
    adapters/openhands/  # external-system boundary (see §3)
  ```
- Within each domain, two fixed roles:
  - `sub_domains/<entity>/<entity>.py` — pydantic `BaseModel` / `@dataclass(frozen=True)` **aggregates** (the nouns).
  - `features/<transition>/<transition>.py` — **pure state-transition functions** (the verbs).
- **Rationale:** one domain per folder keeps each unit independently searchable by a human *and* by an AI agent — a query like "how do X-states transition?" resolves to a single folder. This **formalizes the convention already present** in `.github/scripts/agent_script/` and in `docs/architecture_code.md` (lines 185 and 270); it does not invent a new layout.
- **Packaging is mandatory for the open-source release:** `pyproject.toml` (src layout) declaring `license = "Apache-2.0"`, `requires-python = ">=3.13"`, and **pinned** OpenHands tags (stop installing from `@main`).

### 2.3 Style — "functional programming" = pure state-transition functions over immutable dataclass state

The phrase "functional programming" is used throughout this project's docs but is never defined. This ADR defines it precisely — it is **not** a migration to a functional language; it is a discipline applied to plain Python:

- **State is immutable data.** Every state object is `@dataclass(frozen=True)` (or pydantic `frozen=True`). No in-place mutation.
- **Transitions are pure functions.** A transition is `build_new_state(prev: State, inputs: Inputs) -> NewState`: given a previous state and inputs it returns a *new* state. No side effects in the pure layer; I/O (subprocess, SDK, file, network) is isolated in the adapter/feature layer that calls the pure core.
- **Entities live in `sub_domains/`; behavior lives in `features/`.** The domain model is data; the operations on it are functions. This is the DDD "aggregate + ubiquitous language" split expressed in Python **without OOP ceremony**.
- **Enforced by tooling, not convention.** `mypy --strict` for the type contract; `ruff` rule sets (including mutation bans) to keep in-place mutation from creeping back; `frozen=True` makes the immutability contract compiler-enforced.

**Why dataclasses (not OOP class methods, not string/namespace encodings):** frozen dataclasses give an explicit, typed, immutable field list — ideal for both AI-agent search and the `build_new_state(prev, inputs)` signature — with zero OOP ceremony. A function-only / string-encoding design was considered and rejected because it loses the typed field contract; real immutable dataclasses are the minimal, search-friendly model. (Anti-pattern check satisfied: no class-free / string-namespace design is proposed.)

## 3. Consequences

- The refactor is a **structural** refactor (packaging, layout, typing, adapter boundary) — **not** a language migration.
- `adapters/openhands` becomes the **only** module allowed to `import openhands.sdk`; everything else sees only domain types (`AgentSpec`, `ConversationResult`, `ActionEventRecord`). This makes the language decision **reversible**: if OpenHands later ships a first-class Go/Rust SDK, or the TS client matures, we swap one adapter and keep all domain code.
- The declarative YAML layer and all markdown agent-guidance remain valid, unchanged.
- The open-source release becomes feasible: the license is now **declared** in packaging metadata, and the OpenHands dependency (MIT) is confirmed Apache-2.0-compatible.

## 4. Related

- `docs/architecture_code.md` — the target DDD / functional architecture reference.
- OpenHands SDK license: MIT (Apache-2.0 compatible).
- **This ADR records the *decision* from issue #627. It does not start the refactor** — the step-by-step implementation plan is a separate, later effort.