# Stage A / Step 2: Seam refactors batch 1 — trigger, state fetch, circuit breaker

## Instructions

Parameterize I/O boundaries in three scripts while keeping every GHA default path byte-identical.

1. **`.github/scripts/event_normalizer.py`**: extract a pure function `normalize_trigger(payload: dict) -> Trigger` (import `Trigger` from `engine.models`) that performs exactly the same normalization the current `main()` does over the event payload (issue number, author, body, event type, bot-comment handling). Keep `load_event_data()` (`GITHUB_EVENT_PATH`) and `main()` emitting the same KEY=value lines (`TARGET_NUMBER`, `TRIGGER_BODY`, `TRIGGER_AUTHOR`, `EVENT_TYPE`) with identical values. The `_is_bot_comment` `GITHUB_ACTIONS` env behavior is preserved.

2. **`.github/scripts/resolve_config/features/fetch_state/fetch_state.py`**: keep `extract_state(comments)` pure; change `_fetch_workflow_state_from_history(repo, issue_number)` to accept an optional `history_provider: Callable[[str, int], list[dict]] | None` defaulting to `github_client.get_issue_comments`; thread the provider through the `resolve_config` caller without changing the default result dict.

3. **`.github/scripts/circuit_breaker.py`**: keep pure `should_abort(trigger_body, threshold)` and the CLI/env default path unchanged; add an optional history-provider parameter (callable returning comment body strings) that aggregates the bodies before delegating to the same pure core so marker counting works on any adapter's history.

4. **`loop_guard.py` stays UNCHANGED** (pure over trigger body).

5. **Update tests**: extend `test_event_normalizer.py`, `test_circuit_breaker.py`, `test_fetch_state.py`, `test_resolve_config.py` with provider-injection cases (in-memory fakes, no network) AND keep every existing default-path fixture green unchanged. Add a test asserting `normalize_trigger` output fields equal the KEY=value values `main()` emits for the same fixture event file.

## Constraints

- No `.github/workflows/` edits.
- No behavior change on the GHA path (defaults only).

## Expected outcome

- `normalize_trigger` is importable and pure; the three scripts accept injectable providers with unchanged defaults.
- Full existing test suite in `.github/scripts/` plus the new provider-injection tests pass.

## Relevant files

- `.github/scripts/event_normalizer.py`
- `.github/scripts/resolve_config/resolve_config.py`, `.github/scripts/resolve_config/features/fetch_state/fetch_state.py`
- `.github/scripts/circuit_breaker.py`, `.github/scripts/loop_guard.py` (read-only)
- `engine/models.py` (from step 1)
- `.github/scripts/test_event_normalizer.py`, `.github/scripts/test_circuit_breaker.py`, `.github/scripts/resolve_config/features/fetch_state/test_fetch_state.py` (if present), `.github/scripts/test_resolve_config.py`

## Commit message

`[step 2/6] Stage A / Step 2: Seam refactors batch 1 — trigger, state fetch, circuit breaker`