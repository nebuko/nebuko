# Step 1: Add pinned-vs-summary history selection to query_context

## Title

Step 1: Add pinned-vs-summary history selection to `query_context`

## Instructions

Create `.github/scripts/features/select_history_comments/select_history_comments.py`.

Implement a frozen dataclass `HistorySelection` with fields `verbatim: tuple[dict, ...]` and `summarized: tuple[dict, ...]`, plus properties `verbatim_ids`/`summarized_ids` returning frozensets of comment ids, and a pure function `select_history_comments(comments: list[dict], state: WorkflowState | None) -> HistorySelection`.

- A comment is **verbatim** iff its id is in `state.pinned_comment_ids` **OR** it appears in the `comment_ids` of any `state.history` entry with `pin=True`.
- Every other injectable comment is **summarized**.
- Follow the repo idiom for feature modules: `@dataclass(frozen=True)`, pure functions, small focused helpers. Annotate the class and the pure function with the repo's `# @stub-protected` marker convention when the module carries an Object Design Contract stub.

Wire into `.github/scripts/query_context.py`:

- `_build_history_string` must consume `HistorySelection` instead of rendering every comment verbatim.
- Verbatim comments render their **full body** (after `strip_state_section`).
- Summarized comments render `extract_summary_from_comment(body)` when the `<!-- bot-agent-summary -->` marker is present, otherwise a truncated excerpt capped by a new module constant `HISTORY_EXCERPT_CHARS` (default `400`).
- Keep `_filter_comments_by_inject_context` as the **inclusion gate**; the new object only decides verbatim-vs-summary among included comments.

Add unit tests in `.github/scripts/test_query_context.py` covering:

- pinned id from `pinned_comment_ids` → full body;
- history entry with `pin=True` → full body for all its `comment_ids`;
- non-pinned bot comment with summary marker → summary text only;
- non-pinned comment without marker → excerpt only;
- empty/None state → everything summarized.

## Expected outcome

- `select_history_comments` exists as a pure, importable policy returning `HistorySelection`.
- `query_context._build_history_string` renders pinned comments verbatim and all other included comments as summaries/excerpts.
- Unit tests for the policy and the renderer are present and green.
- Verify with the repo test suite (e.g. `python3 -m unittest .github/scripts/test_query_context.py` or `python3 -m pytest .github/scripts/test_query_context.py`), with no regressions in the broader `.github/scripts/` suite.

## Relevant file context

- `.github/scripts/query_context.py`
  - `extract_summary_from_comment` (L107) — parses the `<!-- bot-agent-summary -->` marker.
  - `strip_state_section` — strips the collapsed workflow-state JSON from a body.
  - `_get_injectable_comment_ids` (L191) — whitelist derived from `HistoryEntry.inject_context`.
  - `_is_bot_comment` (L212), `_filter_comments_by_inject_context` (L229) — the inclusion gate (do not change).
  - `_build_history_string` (L254) — the renderer to refactor.
  - `_build_state_context` (L288) — renders `## Workflow State` incl. the pinned-comments block; be aware it already re-fetches `state.pinned_comment_ids` bodies.
  - `main()` (L369) — assembles filtered comments and calls the two builders.
- `.github/scripts/workflow_state/workflow_state.py` — `WorkflowState.pinned_comment_ids: list[int]` and `history: list[HistoryEntry]`.
- `.github/scripts/workflow_state/sub_domains/history_entry/history_entry.py` — `HistoryEntry.comment_ids: list[int]`, `HistoryEntry.pin: bool = False`.
- `.github/scripts/features/` — existing feature-module layout to mirror (one package per feature, `<name>.py`).
- `.github/scripts/test_query_context.py` — existing tests for history/state rendering.

## Notes

- This step is the read side only. Do not modify `Dispatch`, `build_output.py`, `build_state.py`, or any workflow YAML in this step (those belong to Step 2).
