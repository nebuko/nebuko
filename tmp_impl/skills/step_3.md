# Step 3: seam refactors (inject providers, keep GHA defaults byte-identical)

## Instructions

Refactor the existing pipeline scripts to accept injected providers **WITHOUT**
changing default GHA behavior:

- (a) `agent_script/fetch_state.py` → expose pure `extract_state(comments)` plus
  a history-provider parameter.
- (b) `query_context.py` → provider injection and trigger-object driven (not only
  `--issue-number`); keep `--event-path` working.
- (c) `prepare_comment.py` → accept an injected poster callable; leave
  `select_posted_body` untouched.
- (d) `event_normalizer.py` → expose pure `normalize_trigger(payload) -> Trigger`.
- (e) `issue_creation.py` usage sites → adapter-gated (local mode writes task
  files, no REST calls).
- (f) `circuit_breaker.py` → consume the history provider.

Update the existing unit tests to inject providers. Add regression assertions
that the GHA-path defaults produce byte-identical output to before.

Do **not** modify any GitHub Actions workflow file.

## Expected outcome

- Each listed script exposes the new injection seam while its default (no-injection)
  path stays byte-identical to the pre-refactor behavior.
- `extract_state(comments)` is a pure function; the history provider is injectable.
- `normalize_trigger(payload) -> Trigger` is a pure function reused by both the
  GHA normalizer and the future adapters.
- New/updated tests inject fake providers and pass; regression assertions guard
  byte-identical GHA defaults.
- No diff to any file under `.github/workflows/`.

## Relevant file context

- Real paths (plan names → actual files):
  - `agent_script/fetch_state.py` →
    `.github/scripts/resolve_config/features/fetch_state/fetch_state.py`
    (tests: `.../fetch_state/test_fetch_state.py`)
  - `.github/scripts/query_context.py` (test: `.github/scripts/test_query_context.py`)
  - `.github/scripts/prepare_comment.py` (test: `.github/scripts/test_prepare_comment.py`)
  - `.github/scripts/event_normalizer.py` (test: `.github/scripts/test_event_normalizer.py`)
  - `.github/scripts/issue_creation.py` (test: `.github/scripts/test_issue_creation.py`)
  - `.github/scripts/circuit_breaker.py` (test: `.github/scripts/test_circuit_breaker.py`)
- Models/protocol from Steps 1–2: `engine/models.py`, `adapters/base.py`.
- Consumers whose behavior must not regress: `.github/scripts/loop_guard.py`,
  `.github/scripts/resolve_config/resolve_config.py`.
- Reference (read-only): `.github/workflows/openhands-multi-agent.yml`.
