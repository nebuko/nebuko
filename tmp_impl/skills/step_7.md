# Step 7: Inject '## Human Gate Decision' context section

Issue #648. The human's decision + feedback must reach the routed step (the
re-run gated step, or the successor).

## What to do

When the routed config carries a serialized gate decision
(`config_gate_decision`, produced by step 5), inject a
`## Human Gate Decision` section into the routed step's context containing:

- `status` (approved / rejected)
- `decided_by`
- `feedback`
- `comment_id` when available

This mirrors how `config_human_resume_input` reaches the agent today.

## IMPORTANT — verify the actual injection point first

A repo-wide search shows the literal string `## Human Resume Input` appears
**only** in `defaults/prompts/genius.md` (L171); it is **not** built in the
engine Python. What the engine does is export the value as an env var:

- `src/nebuko/engine/lib/resolve_config/features/build_result/build_result.py`
  L186: `"HUMAN_RESUME_INPUT": config.get("config_human_resume_input", "")`
- `src/nebuko/engine/lib/agent_script/features/build_output/build_output.py`
  L1007: reads `HUMAN_RESUME_INPUT` from the environment.
- The agent's user-context string is assembled in
  `src/nebuko/engine/run_loop.py::_render_context` (L1136) from Author /
  issue body / trigger body / Task / Recent thread.

So implement the section where the engine actually composes the model-facing
context, and add the env plumbing symmetrically to `HUMAN_RESUME_INPUT`:

1. `build_result.py`: add
   `"GATE_DECISION": config.get("config_gate_decision", "")`.
2. `build_output.py` / `run_loop.py`: read it and render the
   `## Human Gate Decision` markdown section (status, decided_by, feedback,
   comment_id). If the section must travel through state, store it in
   `current_context` via `context_updates` in `build_output.py`.
3. Grep for any other place `HUMAN_RESUME_INPUT` is consumed to ensure parity.

## Relevant file context

- `src/nebuko/engine/lib/resolve_config/features/build_result/build_result.py`
  (env mapping, L~150–195).
- `src/nebuko/engine/lib/agent_script/features/build_output/build_output.py`
  (`context_updates` assembled ~L1030–1032; env reads ~L1007).
- `src/nebuko/engine/run_loop.py::_render_context` (L1136) — the user-context
  assembly.

## Tests

- The routed step payload / rendered context contains the
  `## Human Gate Decision` section with the decision fields.
- Absent decision → no section is emitted.

## Expected outcome

- `python -m pytest src/nebuko/engine/lib/resolve_config/features/build_result/ src/nebuko/engine/lib/agent_script/features/build_output/ -q`
  is green.

## Constraints

- Do not break the existing `HUMAN_RESUME_INPUT` plumbing.
- Prefer the smallest surface that both the re-run gated step and the successor
  step consume.
