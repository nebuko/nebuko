# Stage A / Step 5: Driver — engine/run_loop.py + __main__.py

## Instructions

Implement the typed driver composing the pure cores IN-PROCESS (imports, not subprocesses) from the existing `.github/scripts` modules plus the new `engine/models` and `adapters` packages.

**`engine/run_loop.py`**: define `run(adapter: PlatformAdapter, *, workflows_dir, agent_config_path, output_dir, dry_run=False) -> int` executing the canonical 9-step loop:

1. `[phase:normalize]` — `adapter.read_trigger() -> Trigger` (no valid trigger -> terminal).
2. `[phase:resolve]` — call `resolve_config.resolve_config()` with the trigger and history provider (`adapter.load_history`); capture the result dict (`ROLE_NAME`, `WORKFLOW_STEP`, `SKIP_AGENT`, `PROMPT_LOCATION`, `REASON`, `COMPLEXITY`, `MODEL`, etc.).
3. `[phase:guards]` — `loop_guard.should_skip` over the trigger body (skip -> terminal with legacy 'No valid task' style reason) then `circuit_breaker` in history-provider mode (abort -> terminal).
4. `[phase:context]` — `build_query_context(trigger, history_provider, output_dir)` producing `ready_prompt.md`.
5. `[phase:agent]` — call `agent_script.arun` (its existing typed entry; in `dry_run` do NOT execute an LLM — accept a stub final message from a file or argument and still run validate/build stages).
6. `[phase:validate]` — `validate_agent_output(final_message, ROLE_NAME)`.
7. `[phase:post]` — `select_posted_body` (untouched) then `adapter.post`.
8. `[phase:state]` — `build_output`/`save_state`: construct the new full-snapshot `WorkflowState` (schema v1, via the existing `build_state`/`go_next`) and `adapter.save_state(state)`.
9. `[phase:route]` — read the new state's `Dispatch` status and map it to an exit code.

- Emit one structured log line per phase to stdout: `[phase:<name>] <one-line summary>`.
- Define module-level exit-code constants, all distinct and documented:
  - `EXIT_ADVANCE` (next agent must run / workflow continues)
  - `EXIT_HOLD` (waiting for human)
  - `EXIT_TERMINAL` (completed, skipped, aborted, or no valid trigger)

**`engine/__main__.py`**: `python -m engine.run` entry point with minimal args:
- adapter selection: `--local --run-dir runs/<run-id>` for `LocalAdapter`, or GitHub mode via repo/issue env vars
- plus `--dry-run` and `--output-dir`
- wire `workflows_dir` and agent-config paths from the repo root
- do NOT build a `gfa` CLI (Stage C).

**Tests**: a full-loop smoke test with `LocalAdapter` + dry_run stub agent: write `trigger.json` into a temp `runs/<id>/` dir, `run()` returns `EXIT_HOLD` or `EXIT_ADVANCE` per the crafted trigger/workflow, and the journal gains the expected marker-wrapped entries with a valid `WORKFLOW_STATE v1` snapshot that `extract_state` can recover.

## Constraints

- Zero edits to `.github/workflows/`.
- `agent_script/` untouched beyond what already exists.
- `WORKFLOW_STATE v1` byte-identical.

## Expected outcome

- `python -m engine.run --local --run-dir ... --dry-run` runs the full loop end-to-end against the local run dir.
- Smoke test passes: distinct exit codes for advance/hold/terminal; journal markers recoverable by `extract_state`.

## Relevant files

- `engine/models.py`, `engine/__init__.py` (step 1)
- `adapters/base.py`, `adapters/local.py`, `adapters/github.py` (steps 1 & 4)
- `.github/scripts/resolve_config/resolve_config.py`
- `.github/scripts/loop_guard.py`, `.github/scripts/circuit_breaker.py`
- `.github/scripts/query_context.py` (`build_query_context`, step 3)
- `.github/scripts/agent_script/agent_script.py` (`arun`/main flow), `.github/scripts/agent_script/features/build_output/build_output.py`
- `.github/scripts/validate_output.py`
- `.github/scripts/prepare_comment.py` (`select_posted_body`)
- `.github/scripts/workflow_state/workflow_state.py` (`build_state`, `go_next`, `to_git_comment`)
- `workflows/*.yaml`, `agent-config.yaml` (read-only inputs to routing)
- `docs/architecture.md` §Orchestration Loop (canonical 9-step sequence reference)

## Commit message

`[step 5/6] Stage A / Step 5: Driver — engine/run_loop.py + __main__.py`