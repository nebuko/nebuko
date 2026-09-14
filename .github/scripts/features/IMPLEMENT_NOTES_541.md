# Issue #541 — `script:` step implementation notes (checkpoint)

Checkpoint from the `implement` run that hit its time budget during the
read/analysis phase. No code changes yet; this file captures the full
pipeline map so the next run can go straight to coding. DELETE THIS FILE in
the final commit.

## Locked contract (merged via PR #552)

```yaml
steps:
  - name: run-tests
    script:                  # source key = kind, no `kind` field
      path: scripts/test.sh  # required, repo-relative
      args: ["--unit"]       # optional list[str]
      timeout_minutes: 15    # optional int >= 1
    next: review
```

- Schema: `WorkflowStep.script: Optional[ScriptStepSpec]` in
  `config/sub_domains/workflow_step/workflow_step.py` (+ `script_step_spec.py`).
- Env contract resolver → GHA: `RUN_SCRIPT=true`, `SKIP_AGENT=true`,
  `SCRIPT_PATH`, `SCRIPT_ARGS` (space-joined plain string),
  `SCRIPT_TIMEOUT_MINUTES` (empty → default 30), plus existing
  `WORKFLOW_NAME` / `WORKFLOW_STEP`.
- Result semantics: exit 0 → synthetic dispatch `{status: advance, branch: "pass"}`;
  non-zero/timeout → `branch: "fail"`; `conditions[]` route on the branch,
  else declared `next`. Fail ADVANCES in v1 (does not hold).
- Pseudo-role marker: `<!-- bot-agent-script -->`.

## Verified pipeline facts (read this run; line numbers may shift ±few)

### Resolver (`resolve_workflow.py`)
- `resolve_next_from_workflow()` L667: builds final
  `result = {"task": target_step.agent, "step": target_step.name}` (~L787) —
  ADD script branch here: if `target_step.script` is set return
  `{"run_script": True, "script_path": step.script.path, "script_args": step.script.args or [],
   "timeout_minutes": step.script.timeout_minutes, "step": target_step.name}` (NO `task`).
- Sub-workflow markers (`entered_sub_workflow` / `_return_to_parent_marker`)
  are the precedent for marker dicts; `_sub_workflow_entry_marker` at ~L560.
- `_is_terminal_step` check happens before target resolution — script steps
  resolve via the same `next`/`conditions` path as agent steps, so no change
  needed there.

### `resolve_config/features/resolve_route/resolve_route.py`
- `_resolve_dispatch_path` L258: after `wf_result = resolve_next_from_workflow(...)`
  it does `config["task"] = wf_result["task"]` (~L322). Guard: when
  `wf_result.get("run_script")` set, keep `task` empty and attach the script
  fields on config (e.g. `config["run_script"]=True`, `config["script_path"]=...`,
  `config["script_args_str"]=...`, `config["script_timeout_minutes"]=...`) — do
  NOT go through TASK validation.
- `_resolve_workflow_next_step` ~L785: same treatment at its `wf_result`
  consumption site (~L835 `config["task"] = wf_result["task"]`).
- Third call site: the dispatch-sentinel advance branch (~L740,
  `config["task"] = wf_result["task"]` under `elif dispatch.status == "advance"`).
- `_workflow_next_step_skip_reason` (~L874): optionally add a distinct message
  when the unresolved successor is a script step.
- IMPORTANT: `main()` in `resolve_config/resolve_config.py` does
  `if config.get("SKIP_AGENT") == "true": return config` BEFORE
  `_build_result`. A script config has empty task, so `_build_result`'s
  `if not task: return SKIP` would kill it. Solution: detect
  `config.get("run_script")` and build the result dict directly (short-circuit
  before `_build_result`):
  ```
  {TASK: "", ROLE_NAME: "script", AGENT_SELECTED: "false", SKIP_AGENT: "true",
   RUN_SCRIPT: "true", SCRIPT_PATH: ..., SCRIPT_ARGS: ...,
   SCRIPT_TIMEOUT_MINUTES: ..., WORKFLOW_NAME: config["config_workflow"],
   WORKFLOW_STEP: config["config_step"], WORKFLOW_NEXT: <successor>, ...}
  ```
  (All keys consumed by the GHA grep block; keep names stable.)

### GHA (`.github/workflows/openhands-multi-agent.yml`)
- Resolve step (~L204-300) greps each `KEY=` from RESOLVE_OUTPUT into shell vars,
  then writes them to `$GITHUB_ENV`. ADD grep+export lines for
  `RUN_SCRIPT` / `SCRIPT_PATH` / `SCRIPT_ARGS` / `SCRIPT_TIMEOUT_MINUTES`.
- New step "Run Script Step" inserted between "Configure git..." (~L643) and
  "Run OpenHands Agent" (~L649):
  ```yaml
  - name: Run Script Step
    if: env.RUN_SCRIPT == 'true'
    timeout-minutes: 30   # or ${{ ... SCRIPT_TIMEOUT_MINUTES fallback 30 }}
    run: |
      SCRIPTS="${GIT_FLOW_AI_SCRIPTS:-.github/scripts}"
      python3 "$SCRIPTS/features/run_script/run_script.py"
  ```
- "Run OpenHands Agent" guard L649: `env.SKIP_AGENT == 'false'` → add
  `&& env.RUN_SCRIPT != 'true'`.
- Downstream guards keyed on `SKIP_AGENT == 'false'` (circuit breaker L507,
  summarizer gate L555, ack comment L602, query context L628, git PAT config
  L643, prepare comment L740, bot-marker L749, post L813, split-plan L836,
  extra parts L891) all stay false for script runs EXCEPT the posting chain,
  which must run. Plan: change the four posting-chain guards (L740, L749, L813,
  L891) from `SKIP_AGENT == 'false'` to `(SKIP_AGENT == 'false' || RUN_SCRIPT == 'true')`.
  The "Post Terminal Workflow Skip Comment" (L488) is REASON-gated and stays
  quiet (no REASON emitted for script runs). Loop guard (L412) leaves
  SKIP_AGENT untouched when TASK empty.
- Bot-marker inline step (L749): `ROLE_NAME` will be "script" (set by resolver),
  so marker auto-becomes `<!-- bot-agent-script -->`; add "script": "Script"
  to `agent_display_names`. State section + continue-token logic works
  unchanged if the executor writes a valid `workflow_state.json` with
  `dispatch.status=advance`.
- "Prepare Agent Query Context" (L628, gated `SKIP_AGENT=='false'`) does NOT run
  for script runs. So prev state must come from the trigger comment: reuse
  `workflow_state.features.get_last_state.extract_state_from_body`.
  Simplest: executor reads the trigger body from env `TRIGGER_BODY` (already a
  job-level env var, used at L412) and calls
  `extract_state_from_body(os.environ.get("TRIGGER_BODY", ""))`. No network needed.

### Executor (new `features/run_script/run_script.py` + `__init__.py`)
1. Read env: `SCRIPT_PATH` (required), `SCRIPT_ARGS` (optional; split on
   spaces — schema stores list, resolver joins with single spaces; document the
   limitation: no quoted args in v1), `SCRIPT_TIMEOUT_MINUTES` (default 30;
   cap 120 to match agent cap), `WORKFLOW_STEP` (header), `WORKFLOW_NAME`,
   `TRIGGER_BODY`.
2. Safety re-verify (beyond schema): reject leading `/`, any `..` segment via
   `split("/")`, `os.path.realpath` containment inside checkout root, file must
   exist. On any failure: print to stderr, `sys.exit(1)`, write NO artifacts.
3. Execute: `subprocess.run([script, *args], cwd=repo_root, capture_output=True,
   text=True, timeout=minutes*60)`. v1 decision: try direct exec; on
   PermissionError retry via `["/bin/sh", script, *args]` if suffix `.sh`,
   else fail.
4. Non-zero exit / TimeoutExpired = failure RESULT, not crash: still write
   artifacts, exit 0.
5. Comment body written to `final_message.txt` (NOT github_comment.md), so the
   existing prepare→marker→post pipeline runs untouched; `prepare_comment`
   Path A passes it through `clean_message` + `escape_agent_triggers` +
   `_apply_ctx_collapse` (no-op for ROLE!=ctx) and sets HAS_COMMENT:
   ```
   **Script step `{step}` — PASS ✅**   (or `FAIL ⚠️`)
   - Script: `{path}`
   - Args: `{args}` (or none)
   - Exit code: N
   - Duration: Xs
   [TIMED OUT after N minutes banner for timeouts]
   ```
   <raw output verbatim inside a single fenced code block; neutralize embedded
   ``` lines by expanding the fence to ```` >
6. Split via `split_message(msg, 10000)` (verify importable module path at
   runtime); write `final_message.txt` (part 1, NO metrics sentinel), part 2..N
   as `final_message_part_{i}.txt` + `final_message_parts_count.txt` when >1.
7. `workflow_state.json`: load prev via `extract_state_from_body(TRIGGER_BODY)`
   (may be None); use
   `workflow_state.features.build_state.build_state(prev, workflow=WORKFLOW_NAME,
   step=WORKFLOW_STEP, agent="script", phase=_derive_phase(WORKFLOW_STEP)
   (import from build_output or replicate the small helper), summary=...,
   result="pass"/"fail", dispatch=Dispatch(status="advance",
   branch="pass"/"fail"), comment_ids=[])`; serialize with
   `workflow_state.features.to_json.state_to_json(new_state)`; write to
   `./workflow_state.json` (CWD = repo root in the GHA job).
8. Python exit 0 even on script failure (GHA job continues to posting).

### Tests (in `.github/scripts/`)
- `test_run_script.py`: fixture scripts in tmp dir (pass echo, fail exit 1,
  slow sleep). Env monkeypatched. Cover: pass (output verbatim + PASS), fail
  (banner + exit code + dispatch branch fail in workflow_state.json), timeout
  (tiny forced timeout), safety rejects (absolute path, ../x, missing) →
  rc!=0 and no artifacts, overflow >10k splits correctly, state version
  increments from injected TRIGGER_BODY state.
- Extend `test_resolve_workflow_step.py` (pattern: `_import_resolver()` + tmp
  yaml fixtures): workflow with script successor returns run_script marker
  dict (no task key); conditions on fail/pass route correctly.
- Focused test: config with `run_script` yields result dict with
  RUN_SCRIPT/SCRIPT_* and SKIP_AGENT=true, no skip-REASON.

### Docs
- `docs/reference.md` grammar: step source rule incl. nested `script`.
- `docs/workflows.md`: "Script Steps" section (example, pass/fail semantics —
  fail advances by default, security note: runs in same job with job env).
- `docs/architecture.md`: RUN_SCRIPT branch in cascade + pseudo-role marker.

### Commit/PR plan
Single PR, branch `feature/issue-541-script-step-implement`, link issue #541:
1. resolver: `resolve_workflow.py` marker + `resolve_route.py` 3 sites +
   `resolve_config.py` short-circuit + tests
2. executor module + tests
3. GHA wiring + docs (+ delete this notes file)

## Environment
- Python 3.13.3, run pytest from `.github/scripts` (`python -m pytest`).
- Get baseline counts on clean main before starting.
- Pre-existing flake: `test_build_output_graceful_timeout.py::
  test_no_run_outcome_leaves_status_unset` (ordering flake, known).
- Pre-existing sandbox ERROR: `test_circuit_breaker.py::
  test_dry_run_does_not_fail_without_token`.