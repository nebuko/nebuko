# DDD Package-by-Feature Architecture

> **Status:** In progress — canonical implementation in PR #335 (open).  
> **Scope:** `.github/scripts/workflow_state/` package.

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

- [ ] Search for remaining `from state_manager import` statements in `.github/scripts/`
- [ ] Map each imported symbol to its new location in `workflow_state/`
- [ ] Verify `workflow_state.py` facade exports cover all needed symbols
- [ ] Run tests after each import migration: `python -m pytest .github/scripts/ -v`
- [ ] Add `__init__.py` to any new packages created (currently the package uses implicit namespace packages)
- [ ] Remove `state_manager.py` and `test_state_manager.py` once all references are migrated

### Current Known Importers of `state_manager`

| File | Symbols Imported |
|------|-----------------|
| `agent_script.py` | `WorkflowState, HistoryEntry, StateMetrics, IssueInfo, PullRequestInfo, extract_state, build_state, format_collapsed_section, STATE_MARKER_START, STATE_MARKER_END` |
| `query_context.py` | `WorkflowState, HistoryEntry, extract_state` |
| `resolve_config.py` | `extract_dispatch_from_state` |
| `test_resolve_config.py` | `HistoryEntry` |
| `test_state_manager.py` | Various (tests the old module) |

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
