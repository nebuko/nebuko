# Stage A / Step 3: Seam refactors batch 2 — query context, posting, issue creation

## Instructions

Parameterize the remaining three seam scripts; GHA defaults byte-identical.

1. **`.github/scripts/query_context.py`**: extract the current `main()` logic into a public function `build_query_context(trigger: Trigger, *, history_provider, output_dir)` that:
   - loads the event payload,
   - fetches comments via `history_provider`,
   - runs `extract_state`,
   - writes the same artifacts (`prev_workflow_state.json`, `ready_prompt.md`, `issue_body.md`, `comment_history.md`) into `output_dir`,
   - preserves the E2BIG guard behavior (issue #520),
   - preserves recovery-log + `HUMAN_RESUME_INPUT` injection.

   `main()` keeps `--issue-number` (required) and `--event-path`, defaulting `history_provider` to `github_client.get_issue_comments` and `output_dir` to `$RUNNER_TEMP`, with byte-identical outputs.

2. **`.github/scripts/prepare_comment.py`**: add an optional `poster: Callable[[str], None]` parameter to `prepare_comment(...)`; the default poster is `github_client.post_comment` with `ensure_bot_marker`; `select_posted_body` and `merge_handoff_sentinels` are STRICTLY UNTOUCHED; env reads (`TRIGGER_AUTHOR`, `final_message.txt`, `console.log`) and the `github_comment.md` output are preserved.

3. **`.github/scripts/issue_creation.py`**: gate sub-issue creation behind an injectable sink interface (a small Protocol, e.g. `create_task_file(title, body, meta)` or equivalent): keep the existing REST implementation as the default sink so `main()` behavior is unchanged, and add a local sink that writes one task file per sub-issue (JSON: title + body + meta) into a given directory with NO REST calls and no `gh` subprocess.

4. **Update tests**: extend `test_query_context.py`, `test_prepare_comment.py`, `test_issue_creation.py` with provider/poster/sink injection (in-memory fakes, `dry_run` where the existing style uses it); existing default-path fixtures stay green unchanged.

## Constraints

- No `.github/workflows/` edits.
- No behavior change on the GHA default path.

## Expected outcome

- `build_query_context` is importable with injected provider; `prepare_comment` accepts an injected poster; `issue_creation` accepts an injected sink (REST default, local-file alternative).
- Full existing test suite plus the new injection tests pass.

## Relevant files

- `.github/scripts/query_context.py`
- `.github/scripts/prepare_comment.py`
- `.github/scripts/issue_creation.py`
- `.github/scripts/github_client.py` (read-only reference: `get_issue_comments`, `post_comment`, `ensure_bot_marker`)
- `engine/models.py` (from step 1)
- `.github/scripts/test_query_context.py`, `.github/scripts/test_prepare_comment.py`, `.github/scripts/test_issue_creation.py`

## Commit message

`[step 3/6] Stage A / Step 3: Seam refactors batch 2 — query context, posting, issue creation`