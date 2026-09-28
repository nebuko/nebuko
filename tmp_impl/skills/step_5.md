# Step 5: engine.run_loop driver

## Instructions

Create `engine/run_loop.py` with frozen
`RunResult{status:str, next_agent:str|None, exit_code:int}` and
`Engine(adapter, config)` exposing `run() -> RunResult` implementing the loop:

normalize → `resolve_config` → guards (`loop_guard`, `circuit_breaker`) → build
prompt → `agent_script.arun` → `validate_output` → `select_posted_body` + `post`
→ `save_state` → route next.

- Emit structured `[phase:*]` log markers matching the legacy bash's observable
  markers.
- Exit codes distinguish **advance / hold / terminal**.
- Add `engine/__main__.py` as a thin module entry (library-only; no CLI surface
  beyond `python -m engine.run_loop` for tests).
- Tests drive `Engine` with `LocalAdapter` and fixture payloads end-to-end.

No workflow-file changes.

## Expected outcome

- `Engine(adapter, config).run()` returns a `RunResult` whose `status`
  (`advance`/`hold`/`terminal`) and `exit_code` match the legacy loop's outcomes.
- `[phase:*]` log lines are emitted at the same observable points as the legacy
  bash loop.
- End-to-end test with `LocalAdapter` + fixture payload produces the expected
  posting and state persistence through the adapter.
- `python -m engine.run_loop` works as a thin entry (no new user-facing CLI).
- No diff outside `engine/` (+ new tests).

## Relevant file context

- Seams from Step 3: `agent_script` arun path, `fetch_state`, `query_context`,
  `prepare_comment.select_posted_body`, `event_normalizer.normalize_trigger`,
  `circuit_breaker`.
- Guards: `.github/scripts/loop_guard.py`, `.github/scripts/circuit_breaker.py`.
- Config resolution: `.github/scripts/resolve_config/resolve_config.py`,
  `.github/scripts/resolve_workflow.py`.
- Output validation: `.github/scripts/validate_output.py`.
- Agent runner: `.github/scripts/agent_script/agent_script.py` and
  `.github/scripts/agent_script/features/run_agent/run_agent.py`.
- Adapters: `adapters/local.py`, `adapters/github.py`.
- Legacy loop semantics (read-only reference):
  `.github/workflows/openhands-multi-agent.yml` (job `run-task`).
