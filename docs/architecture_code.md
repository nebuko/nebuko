# DDD Package-by-Feature Architecture

> **Status:** In progress — canonical implementation in PR #335 (open).  
> **Scope:** `.github/scripts/workflow_state/`, `.github/scripts/resolve_config/`, `.github/scripts/config/`, and `.github/scripts/agent_script/` packages.

---

## 1. Overview

The `workflow_state` module uses a **DDD package-by-feature** architecture. Domain entities live in bounded-context folders (`sub_domains/`), while cross-cutting behavioral features reside in `features/`. The root `workflow_state.py` acts as a **facade**, providing a single import point for all public symbols.

This replaces the previous monolithic `state_manager.py` module, which contained all entities, behaviors, and utilities in a single file (~500 lines).

**Reference:** PR #335 ([split feature per folder](https://github.com/olivier-norture/git-flow-ai/pull/335))

### Directory Structure

```
.github/scripts/workflow_state/
├── workflow_state.py                          # Facade: facade + WorkflowState model + convenience functions
├── test_workflow_state.py                     # Facade-level tests
├── features/                                  # Cross-cutting features (span multiple sub-domains)
│   ├── build_state/
│   │   ├── build_state.py                     # build_state() — pure state transition
│   │   └── test_build_state.py
│   ├── comment/
│   │   └── to_git_comment.py                  # format_collapsed_section(), wrap_comment_with_state()
│   ├── get_last_state/
│   │   └── get_last_state.py                 # extract_state_from_body(), extract_latest_state_from_comments()
│   ├── legacy/
│   │   └── legacy.py                          # Placeholder for future legacy adapters
│   └── to_json/
│       └── to_json.py                         # state_to_json()
└── sub_domains/                               # Bounded contexts (domain entities)
    ├── history_entry/
    │   └── history_entry.py                   # HistoryEntry model
    ├── issue_info/
    │   └── issue_info.py                      # IssueInfo model
    ├── pull_request_info/
    │   └── pull_request_info.py               # PullRequestInfo model
    ├── state_metrics/
    │   ├── state_metrics.py                   # StateMetrics model
    │   └── features/
    │       └── legacy/
    │           └── legacy.py                  # Sub-domain-specific legacy placeholder
    └── dispatch/
        ├── dispatch.py                        # Dispatch model
        └── features/
            └── get_dispatch_from_state/
                └── get_dispatch_from_state.py # Extract dispatch from WorkflowState
```

---

## 2. Directory Structure Rules

### `sub_domains/` — Bounded Contexts

Each sub-domain is a **bounded context** containing:
- **Entity/model file**: A Pydantic `BaseModel` defining the domain object (e.g., `history_entry.py` → `HistoryEntry`).
- **Optional `features/` sub-folder**: Behaviors specific to that entity only (e.g., `dispatch/features/get_dispatch_from_state/`). These features are *not* shared across sub-domains.

**Rule:** Entities in `sub_domains/` are simple data models (Pydantic `BaseModel`). They may contain validators and factory methods (`@staticmethod`) but should not contain business logic workflows.

### `features/` — Cross-Cutting Features

The root `features/` folder contains **behavioral modules** that span multiple sub-domains. Examples:
- `build_state/` — State transitions that combine `HistoryEntry`, `StateMetrics`, `IssueInfo`, etc.
- `to_json/` — Serialization of any `WorkflowState`.
- `get_last_state/` — Extraction of state from GitHub comments.
- `comment/` — Formatting state for GitHub comment output.

**Rule:** Features in root `features/` are **use cases / operations** that may import from multiple `sub_domains/`. They represent pure state-transition functions or I/O adapters.

### Facade — `workflow_state.py`

The root `workflow_state.py` serves as the **single import point**. It:
1. Defines the core `WorkflowState` model (the aggregate root).
2. Re-exports key symbols from `features/` and `sub_domains/` via direct imports.
3. Provides convenience wrapper functions (e.g., `to_json()`, `to_git_comment()`) that delegate to feature modules.
4. Exports constants (e.g., `STATE_MARKER_START`, `STATE_MARKER_END`).

**Rule:** External consumers should prefer importing from the facade rather than deep-importing into feature sub-modules.

---

## 3. Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Modules | Match parent folder name | `features/build_state/build_state.py` |
| Classes | PascalCase | `WorkflowState`, `HistoryEntry`, `Dispatch` |
| Functions | snake_case | `build_state`, `format_collapsed_section`, `state_to_json` |
| Constants | UPPER_SNAKE_CASE | `STATE_MARKER_START`, `DISPATCH_STATUSES` |
| Test files | `test_` prefix, co-located with source | `features/build_state/test_build_state.py` |

---

## 4. Import Patterns

### Preferred: Facade Imports

External consumers (e.g., `agent_script.py`, `query_context.py`) should import from the facade:

```python
from workflow_state import (
    WorkflowState,
    HistoryEntry,
    StateMetrics,
    IssueInfo,
    PullRequestInfo,
    Dispatch,
    build_state,
    to_json,
    to_git_comment,
    from_body,
    STATE_MARKER_START,
    STATE_MARKER_END,
)
```

### Deep Imports (Internal Use)

Feature modules may deep-import from sibling sub-domains using relative imports:

```python
# In features/build_state/build_state.py:
from ...workflow_state import WorkflowState
from ...sub_domains.history_entry.history_entry import HistoryEntry
from ...sub_domains.state_metrics.state_metrics import StateMetrics
```

### Never Do

- **Never import from the deleted `state_manager` module.** The old monolith has been superseded by `workflow_state`.
- **Never use absolute imports** within the `workflow_state` package. Always use relative imports (`...`) to reference sibling modules.

---

## 5. Migration Checklist for @@dev

When migrating remaining code from `state_manager` to `workflow_state`:

- [x] Search for remaining `from state_manager import` statements in `.github/scripts/`
- [x] Map each imported symbol to its new location in `workflow_state/`
- [x] Verify `workflow_state.py` facade exports cover all needed symbols
- [x] Run tests after each import migration: `python -m pytest .github/scripts/ -v`
- [x] Add `__init__.py` to any new packages created (currently the package uses implicit namespace packages)
- [x] Remove `state_manager.py` and `test_state_manager.py` once all references are migrated

Migration completed. All consumers now import from `workflow_state/`. `state_manager.py` has been removed. `test_state_manager.py` never existed.

---

## 6. Design Decisions & Trade-offs

### Why Explicit Re-exports Over Implicit Namespace Packages?

The `workflow_state` package currently uses **implicit namespace packages** (no `__init__.py`). All re-exporting happens through `workflow_state.py` at the root. This was chosen for:

- **Clarity** — One file (`workflow_state.py`) documents all public symbols.
- **IDE support** — Import resolution works without requiring `__init__.py` in every sub-folder (Python 3.3+ namespace packages).
- **Minimal boilerplate** — Fewer files to maintain.

**Trade-off:** If a consumer needs a symbol that isn't re-exported by the facade, they must deep-import. The facade should be kept up-to-date as new symbols are added.

### Why Separate `sub_domains/features/` from Root `features/`?

Sub-domain-level features (e.g., `dispatch/features/get_dispatch_from_state/`) are kept separate from root `features/` to enforce **bounded context separation**:

- Sub-domain features only depend on their parent sub-domain's entity.
- Root features may depend on multiple sub-domains (they are cross-cutting).
- This prevents accidental coupling between sub-domains.

### Why Pydantic `BaseModel` for Entities?

All domain entities (`HistoryEntry`, `StateMetrics`, `IssueInfo`, `PullRequestInfo`, `Dispatch`) are Pydantic models, providing:
- Validation at construction time.
- Built-in serialization (`model_dump`, `model_validate`).
- Default values and field documentation.
- Field validators (`@field_validator`) for constraint enforcement.

### Pure State-Transition Functions

Functions like `build_state()` follow a functional pattern: given a previous state + inputs, return a **new** state. No in-place mutation occurs. This enables:
- Predictable behavior (same inputs → same outputs).
- Easy testing (pure I/O contracts).
- Rollback safety (previous states remain intact).

---

## 7. `config/` package

DDD package-by-feature layout for the layered YAML configuration (`agent-config.yaml`) and workflow definitions. Like the other packages, `__init__.py` re-exports the public surface so consumers can import from the package root.

```
.github/scripts/config/
├── __init__.py                                # Package facade: re-exports all public symbols
├── config.py                                  # Facade constants (CONFIG_DEFAULT_PATH)
├── features/
│   ├── load_config/load_config.py             # load_config() — parse + validate agent-config.yaml
│   ├── load_workflow/load_workflow.py         # load_workflow(), resolve_workflow_reference()
│   └── resolve_refs/resolve_refs.py           # resolve_refs(), REF_PATTERN
└── sub_domains/                               # Bounded contexts (Pydantic entities)
    ├── provider_config/                       # ProviderConfig: api_url + api_token_env
    ├── llm_config/                            # LLMConfig: model_path, provider ref, sampling params
    ├── agent_config/                          # AgentConfig: model ref, prompt_file, skills, tools
    ├── app_config/                            # AppConfig: the full providers/llms/agents tree
    ├── workflow_definition/                   # WorkflowDefinition: name, steps, trigger
    ├── workflow_step/                         # WorkflowStep: agent/workflow, next, conditions, skill
    ├── workflow_trigger/                      # WorkflowTrigger: trigger.agents
    └── workflow_condition/                    # WorkflowCondition: on_result → next
```

**Key behaviors:**

- `load_config(path)` — parses and validates the layered config into `AppConfig`; fails fast on schema violations.
- `load_workflow(path)` — loads a `workflows/*.yaml` file into a `WorkflowDefinition`. Also handles workflow *references* (`resolve_workflow_reference`), where a step's `workflow:` value may be a workflow name or a path like `tmp_impl/workflow.yaml`.
- `resolve_refs(config)` — resolves `${layer.key.subkey}` references (see [configuration.md](./configuration.md#ref-cross-layer-resolution)):
  - Full-string ref (`"${providers.openrouter}"`) → replaced by the referenced object itself.
  - Inline partial ref (`"prefix_${llms.x.model_path}"`) → string-only substitution.
  - Recurses through dicts and lists; an undefined ref raises at load time.

**Note:** the workflow grammar documented in [reference.md](./reference.md#workflow-yaml-grammar) is enforced here by the `workflow_*` sub-domain entities.

---

## 8. `agent_script/` package

The OpenHands execution layer. Entry point `agent_script.py` is a slim orchestrator that chains the features below; all heavy lifting lives in `features/`.

```
.github/scripts/agent_script/
├── agent_script.py                            # Slim entry point (orchestrates the features)
├── features/
│   ├── load_prompt/                           # Load system prompt from the agent's prompt_file
│   ├── resolve_llm_config/                    # Resolve provider/LLM config + dispatch overrides
│   ├── run_agent/                             # Agent/Conversation creation + supervised execution
│   │   ├── run_agent.py                       #   async arun() under a watchdog
│   │   ├── run_watchdog.py                    #   soft-timeout watchdog (interrupt())
│   │   ├── timeout_budget.py                  #   AGENT_SOFT_TIMEOUT_MINUTES / WIND_DOWN_TIMEOUT_MINUTES
│   │   ├── wind_down_prompt.py                #   conclusion prompt sent when budget elapses
│   │   └── run_outcome.py                     #   RunOutcome classification (finished/interrupted/...)
│   ├── build_output/                          # Build final_message.txt + fresh WorkflowState snapshot
│   ├── collect_metrics/                       # Extract the agent-metrics payload from the run
│   ├── ensure_pr/                             # Deterministic PR fallback (commit/push/open PR)
│   ├── extract_recovery_trace/                # Capture the tail of interrupted runs
│   └── extract_split_plan/                    # Pull <splitPlan> blocks out of the final message
└── sub_domains/                               # Pure-function bounded contexts
    ├── message_splitter/                      # split_message() — paragraph/line/sentence-boundary splits
    ├── metrics/metrics_collector.py           # Metrics collection helpers
    ├── prompt/                                # Prompt assembly helpers
    └── split_plan/                            # extract_split_plan()/strip_split_plan() pure functions
```

**Soft-timeout escalation chain** (issue #489):

```
arun() under watchdog ──budget elapsed──► conversation.interrupt()
        │                                        │
     completes                              wind-down run (own budget)
        │                                        │ uncooperative / budget over
        ▼                                        ▼
   build_output + metrics                ensure_pr (git commit → push → open PR, no LLM)
                                            + extract_recovery_trace
```

**Design notes:**

- `sub_domains/` here are *pure function modules* (e.g., `split_message`, `extract_split_plan`) rather than Pydantic entities — the "entity vs behavior" split of §2 still applies, the entities simply have shape.
- All external side effects (LLM calls, git, gh CLI) are confined to the feature layer; sub-domain functions are pure I/O and unit-tested in isolation.
- Feature details for users are in [features.md — Execution Layer](./features.md#execution-layer).
