# ADR-0001: Language Choice (Python 3.13) and Functional DDD Package-by-Feature Design

**Status:** Accepted
**Date:** 2026-09-22
**Deciders:** Olivier Norture (author), @architect (architecture)
**Scope:** `gitflow-ai` (now Nebuko) open-source + commercial (Apache-2.0) refactoring baseline
**Records the thinking from:** issue #627

## 1. Context

`gitflow-ai` (now Nebuko) is currently a 100% Python codebase: 161 `.py` files, ~30,164 LOC, 55 test files, and **no packaging metadata** (no `pyproject.toml`/`setup.py`/`requirements.txt`).

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

- A real package rooted at `src/nebuko/`, **one folder per domain**:
  ```
  src/nebuko/
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
- **Rationale:** one domain per folder keeps each unit independently searchable by a human *and* by an AI agent — a query like "how do X-states transition?" resolves to a single folder. This **formalizes the convention already present** in `engine/lib/agent_script/` and in `docs/architecture_code.md` (lines 185 and 270); it does not invent a new layout.
- **Packaging is mandatory for the open-source release:** `pyproject.toml` (src layout) declaring `license = "Apache-2.0"`, `requires-python = ">=3.13"`, and **pinned** OpenHands tags (stop installing from `@main`).

### 2.3 Style — "functional programming" = pure state-transition functions over immutable dataclass state

The phrase "functional programming" is used throughout this project's docs but is never defined. This ADR defines it precisely — it is **not** a migration to a functional language; it is a discipline applied to plain Python:

- **State is immutable data.** Every state object is `@dataclass(frozen=True)` (or pydantic `frozen=True`). No in-place mutation.
- **Transitions are pure functions.** A transition is `build_new_state(prev: State, inputs: Inputs) -> NewState`: given a previous state and inputs it returns a *new* state. No side effects in the pure layer; I/O (subprocess, SDK, file, network) is isolated in the adapter/feature layer that calls the pure core.
- **Entities live in `sub_domains/`; behavior lives in `features/`.** The domain model is data; the operations on it are functions. This is the DDD "aggregate + ubiquitous language" split expressed in Python **without OOP ceremony**.
- **Enforced by tooling, not convention.** `mypy --strict` for the type contract; `ruff` rule sets (including mutation bans) to keep in-place mutation from creeping back; `frozen=True` makes the immutability contract compiler-enforced.

**Why dataclasses (not OOP class methods, not string/namespace encodings):** frozen dataclasses give an explicit, typed, immutable field list — ideal for both AI-agent search and the `build_new_state(prev, inputs)` signature — with zero OOP ceremony. A function-only / string-encoding design was considered and rejected because it loses the typed field contract; real immutable dataclasses are the minimal, search-friendly model. (Anti-pattern check satisfied: no class-free / string-namespace design is proposed.)

**Implementation refinement (recorded 2026-09-22, issue #630):** `WorkflowState` is persisted as JSON in the workflow-state comment and round-tripped on every run, so its immutability is enforced with pydantic `ConfigDict(frozen=True)` rather than a bare frozen dataclass — a bare `@dataclass` would break that serialization. The state core will also use explicit `Phase`/`Step` str-enums (values mirroring the strings in `workflows/*.yaml`) consumed by the pure `build_state` transition, rejecting unknown transitions with a typed error.

### 2.4 OpenHands SDK Usage Surface

Added 2026-09-22 in response to the challenge of this ADR (issue #630), which questioned whether the OpenHands integration is "just an exec call with a prompt in and the answer parsed from the log." Verified evidence: **it is not.** Seven distinct SDK capabilities are in use, and answer extraction consumes the SDK's typed event model — raw log parsing is deliberately avoided.

| # | Capability | Location | Why it matters |
|---|---|---|---|
| 1 | **Type-level tool extension** — `DispatchNextTool` subclasses SDK `ToolDefinition`/`Action`/`Observation`/`ToolExecutor` | `engine/lib/agent_script/features/dispatch_next_tool/dispatch_next_tool.py` | The entire agent hand-off/dispatch protocol is a *custom SDK tool*. A REST/WS call cannot express this; the alpha TS client cannot express it at all. |
| 2 | **Async conversation lifecycle** — `Conversation.arun()` under an asyncio loop | `engine/lib/agent_script/features/run_agent/run_agent.py` (L96–98) | A black-box process "exec" gives no mid-run handle. |
| 3 | **`conversation.interrupt` watchdog** — budget enforcement mid-conversation | `run_agent.py` (L93) | Cannot be reproduced faithfully against a black box; timeouts would be guessed. |
| 4 | **Context condenser** — `get_default_condenser(llm)` wired per-conversation | `run_agent.py` (L199) | Internal SDK behavior, not exposed through REST. |
| 5 | **Event-stream answer extraction** — `get_agent_final_response(conversation.state.events)`, iteration over `ActionEvent`/`MessageEvent`, `content_to_str` | `engine/lib/agent_script/features/build_output/build_output.py` (L435+, L664) | The typed SDK event model is consumed; migrating to a non-Python surface means re-implementing an event normalizer against log text or WebSocket payloads. |
| 6 | **In-process SDK state access** for the dispatch-recall loop | `run_agent.py` (L248–293) | `conversation.state` is in-process data; remotely it is an extra API surface. |
| 7 | **Per-role SDK tools** (`terminal`, `file_editor`, `task_tracker`) configured declaratively | `agent-config.yaml` (L66–75) | Available first-class only in the Python SDK. |

**Import footprint.** 14+ files import `openhands.sdk`; exactly **one** of those files performs a type-level extension (the custom tool in capability #1); the rest go through `Agent`/`Conversation`/`LLM` construction and SDK event types. This footprint is the concrete blast radius any language migration would touch.

**Integration risk notes.**

- **Unpinned SDK install.** CI installs `openhands-sdk` and `openhands-tools` from `git+https://github.com/OpenHands/software-agent-sdk.git@main` with no ref pin (`.github/workflows/nebuko-workflow.yaml`, L168–169). Any upstream commit can break the build. Recorded as a known risk tied to the Python-SDK choice; pinning to a fixed commit SHA / version tag is a follow-up action (see §5).
- **TS client status.** The OpenHands TypeScript client is explicitly **alpha** ("not stable, API may change significantly without notice"); its standalone `OpenHands/typescript-client` repo was **archived** (2026-08-30) and the client now lives under `clients/typescript` in `software-agent-sdk` with an early-testing disclaimer. This is the basis for rejecting TypeScript *now* — not in principle.

## 3. Consequences

- The refactor is a **structural** refactor (packaging, layout, typing, adapter boundary) — **not** a language migration.
- `adapters/openhands` becomes the **only** module allowed to `import openhands.sdk`; everything else sees only domain types (`AgentSpec`, `ConversationResult`, `ActionEventRecord`). This makes the language decision **reversible**: if OpenHands later ships a first-class Go/Rust SDK, or the TS client matures, we swap one adapter and keep all domain code.
  - **Status (as of 2026-09-22): this single-boundary clause is *not yet implemented*.** The SDK is currently imported directly in ~14 files (`run_agent.py`, `dispatch_next_tool.py`, `build_output.py`, and others). Building the boundary is an open action item (§5), not a current-state claim.
- **Revisit trigger** (recorded 2026-09-22, issue #630): re-open the language decision of §2.1 when **any** of the following holds —
  (a) the OpenHands TypeScript client leaves alpha and supports type-level custom tools;
  (b) OpenHands ships a first-class non-Python SDK (e.g. Go/Rust);
  (c) the `adapters/openhands` boundary of this section proves to be a maintenance burden over 2 consecutive migrations.
- The declarative YAML layer and all markdown agent-guidance remain valid, unchanged.
- The open-source release becomes feasible: the license is now **declared** in packaging metadata, and the OpenHands dependency (MIT) is confirmed Apache-2.0-compatible.

## 4. Related

- `docs/architecture_code.md` — the target DDD / functional architecture reference.
- OpenHands SDK license: MIT (Apache-2.0 compatible).
- **This ADR records the *decision* from issue #627. It does not start the refactor** — the step-by-step implementation plan is a separate, later effort.

## 5. Deferred follow-ups (tracked in separate issues)

The challenge of this ADR (issue #630) was resolved with a documentation amendment only, per the maintainer's scoping decision. The approved design work below is **not** part of that amendment and is deferred to dedicated issues:

1. **`adapters/openhands` boundary** — the single SDK-importing package: `OpenHandsAdapter` (owns `Agent`/`Conversation`/`LLM` construction, `arun` lifecycle, interrupt watchdog, event→domain projection) plus frozen domain contracts `AgentSpec` / `ConversationResult` / `ActionEventRecord`. Design per the approved proposal of issue #630; `DispatchNextTool` moves inside the adapter package as an SDK implementation detail.
2. **Frozen state core** — `WorkflowState` immutability via pydantic `ConfigDict(frozen=True)` (see §2.3 refinement) plus explicit `Phase`/`Step` str-enums with typed-transition validation in the pure `build_state` function.
3. **CI gates** — `mypy --strict` job scoped to `adapters/` and `workflow_state/` (repo-wide strict deferred), plus a boundary-guard check failing on any `import openhands` outside `engine/lib/adapters/`.
4. **SDK pinning** — pin `openhands-sdk` / `openhands-tools` in CI to a fixed commit SHA / version tag, replacing the unpinned `@main` install flagged in §2.4.