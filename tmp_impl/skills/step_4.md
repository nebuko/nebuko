# Stage A / Step 4: LocalAdapter + GitHubAdapter

## Instructions

Implement both adapters against the `PlatformAdapter` protocol from `adapters/base.py` and the models from `engine/models.py`.

1. **`adapters/local.py`**: `class LocalAdapter(run_dir: Path)` operating on `runs/<run-id>/` with files `trigger.json`, `journal.md`, `state.json`, `latest_answer.md`, `console.log`.
   - `read_trigger()` parses `trigger.json` into `Trigger`.
   - `load_history()` parses `journal.md` into `list[CommentEntry]`. The journal format is append-only entries, each carrying the EXACT standard markers the GHA `add-comment` action adds today: a leading `<!-- bot-workflow-terminal -->` line plus an optional `<!-- bot-agent-<role> -->` line mirroring the `tag:` param — so `loop_guard`'s regex `<!-- bot-agent-([a-z][a-z-]*) -->`, `circuit_breaker`'s marker counts, `summarizer_gate`, and `extract_state` all work unchanged.
   - `post(body)` appends such an entry with the current ISO-8601 timestamp and the bot author; provide a way to include the `bot-agent-<role>` tag line for agent-run posts.
   - `load_state()` = `extract_state(load_history())`.
   - `save_state(state)` appends `to_git_comment(state)` as a journal entry AND writes a `state.json` mirror.
   - The adapter also mirrors `latest_answer.md` and `console.log` from `agent_script` outputs when present (copy, do not transform).

2. **`adapters/github.py`**: `class GitHubAdapter(repo, issue_number, event_path=None, dry_run=False)` COMPOSING (not rewriting) the existing `github_client` primitives: `get_issue_comments`, `post_comment`, `ensure_bot_marker`.
   - `read_trigger()` loads `event_path` (default: `os.environ` `GITHUB_EVENT_PATH`) and calls `normalize_trigger` from `event_normalizer` (step 2).
   - `load_history()` maps raw comment dicts to `CommentEntry` (author, body, created_at).
   - `post(body)` = `ensure_bot_marker` + `post_comment` (respect `dry_run`).
   - `load_state()` = `extract_state(load_history())`.
   - `save_state(state)` = `post(to_git_comment(state))`.

3. **Tests**:
   - `adapters/test_local.py` — journal marker fidelity (a posted agent-run entry is matched by `loop_guard`'s regex and by `circuit_breaker`'s counts; `extract_state` recovers a saved state from the journal; `save_state` → `load_state` round-trip).
   - `adapters/test_github.py` — using the existing test style (mock `gh` at the `github_client` import level / `dry_run=True`, no network).

## Constraints

- Marker catalog byte-identical.
- `github_client.py` itself may only gain nothing or trivial re-exports — composition over modification.
- No `.github/workflows/` edits.

## Expected outcome

- Both adapters satisfy `PlatformAdapter` and their tests pass, including marker-fidelity checks against the real `loop_guard`/`circuit_breaker`/`extract_state` modules.

## Relevant files

- `adapters/base.py`, `engine/models.py` (from step 1)
- `.github/scripts/github_client.py` (composition base)
- `.github/scripts/event_normalizer.py` (`normalize_trigger`, from step 2)
- `.github/scripts/loop_guard.py`, `.github/scripts/circuit_breaker.py`, `.github/scripts/summarizer_gate.py` (read-only, marker consumers)
- `.github/scripts/workflow_state/workflow_state.py` (`to_git_comment`), `.github/scripts/workflow_state/features/get_last_state/get_last_state.py` (`extract_state`)
- `.github/scripts/test_github_client.py` (test style reference)

## Commit message

`[step 4/6] Stage A / Step 4: LocalAdapter + GitHubAdapter`