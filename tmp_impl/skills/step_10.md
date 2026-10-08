# Step 10: Green the full test suite and verify gate invariants

Issue #648. Final validation across all previous steps.

## What to do

1. Run the full suite and make it green:
   ```
   python -m pytest src/nebuko -q
   ```
   (`pyproject.toml` sets `testpaths = ["tests"]` and
   `pythonpath = ["src", "src/nebuko/engine/lib"]`; run the co-located suites
   under `src/nebuko` explicitly as above, plus `python -m pytest -q` for the
   root `tests/`.)
2. Fix regressions in the gate/resume suites:
   - `src/nebuko/engine/lib/agent_script/features/build_output/test_build_output.py`
     (`TestHumanGateIntercept`, L321–511)
   - `src/nebuko/engine/lib/agent_script/features/build_output/test_build_output_soft_kill.py`
     (L137–148)
   - `src/nebuko/engine/lib/resolve_config/features/resolve_route/test_resolve_route.py`
     (L328–547) — the gate-precedence tests using
     `reason_code="gate_hold"` must keep passing.
   - `src/nebuko/engine/lib/test_resolve_config.py`
     (resume/gate routing tests).
3. Confirm the three invariant tests exist **and** pass:
   - **(a)** a comment authored by the bot account containing
     `/nebuko approve gate` is inert (`may_decide_gate()` is False / routing
     no-ops).
   - **(b)** a human approve consumed twice is a no-op the second time
     (single-shot).
   - **(c)** `/nebuko resume` on a `human_gate:*` hold replies with the pointer
     to `/nebuko approve gate` / `/nebuko reject gate` and the workflow stays
     held.

## Relevant file context

- `pyproject.toml` `[tool.pytest.ini_options]` (L~33–36).
- Test suites listed above; root `tests/` engine integration tests:
  `tests/engine/` (incl. `test_lint.py`, `test_run_loop.py`).

## Expected outcome

- Full suite green; the three invariant tests above present and passing.
- No `# @stub-protected` signature modified anywhere.

## Constraints

- Only fix genuine regressions caused by this change; do not refactor
  unrelated code.
- If a failing test reveals a design gap, stop and report the blocker rather
  than papering over it.
