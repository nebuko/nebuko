# Step 4: GitHubAdapter composing existing gh/REST clients

## Instructions

Create `adapters/github.py` with `GitHubAdapter(event_path=None, client=None)`
implementing `PlatformAdapter` by composing the existing `github_client` / `gh`
REST clients and `event_normalizer.normalize_trigger`.

- `read_trigger` parses the JSON at `GITHUB_EVENT_PATH` via `normalize_trigger`.
- `load_history` fetches issue/PR comments into `CommentEntry`.
- `post` uses the existing comment-posting path.
- `load_state`/`save_state` reuse the existing state comment read/write.
- Tests run against **recorded payload fixtures (no live network)**.

No workflow-file changes.

## Expected outcome

- `adapters/github.py` exposes `GitHubAdapter` structurally conforming to
  `PlatformAdapter`.
- `read_trigger` yields a `Trigger` equal to `normalize_trigger(payload)` for a
  fixture payload.
- `load_history`/`post`/`load_state`/`save_state` delegate to the injected client
  (so tests run fully offline against fixtures).
- Tests pass with recorded payload fixtures; no network access.
- No diff outside `adapters/` (+ new tests/fixtures).

## Relevant file context

- Protocol to implement: `adapters/base.py`; models: `engine/models.py`.
- Compose existing clients:
  - `.github/scripts/github_client.py` (tests: `.github/scripts/test_github_client.py`)
  - `.github/scripts/event_normalizer.py` (`normalize_trigger` from Step 3)
  - existing comment-posting path in `.github/scripts/prepare_comment.py`
  - existing state-comment read/write used by
    `.github/scripts/resolve_config/resolve_config.py`
- `GITHUB_EVENT_PATH` payload shape: see `.github/scripts/event_normalizer.py`
  and `.github/scripts/agent_script/` consumers.
- New files: `adapters/github.py` + tests (e.g. `adapters/test_github.py`) and
  recorded fixtures under `adapters/fixtures/`.
