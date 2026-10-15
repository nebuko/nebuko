## Dev environment
Python library (requires Python >= 3.12). Pick one, from the repo root:
- **uv (preferred):** `uv sync`. The default `dev` dependency group in
  `pyproject.toml` installs `pytest` + `pytest-mock`, so this is the *only*
  setup step. Run everything with `uv run ...`.
- **nix:** `nix develop`.

Do not install test tooling ad hoc (`pip install pytest`, ...): it is declared in
`pyproject.toml` and `flake.nix`.

## Code language
This project is in Python.

## Running the test suite
`pyproject.toml` sets `testpaths = ["tests"]`, so a bare `pytest` collects **only**
`tests/`. The flat runtime modules under `src/nebuko/engine/lib/**` hold their own
co-located `test_*.py` files and are **not** collected by default.

```bash
uv run pytest                              # fast gate: tests/ only
uv run pytest src/nebuko/engine/lib        # runtime engine/lib unit tests
uv run pytest tests src/nebuko/engine/lib  # everything
```

Both suites are green on `main` (they only need `defaults/` in the checkout), so a
failure is a real regression - do not spend turns proving a failure is "pre-existing"
without first diffing against `origin/main`.

## Where workflows live
Workflow definitions ship in `defaults/workflows/` (e.g. `default.yaml`); per-repo
overrides go in `.nebuko/workflows/`. Resolve them through
`engine/lib/default_paths.py::config_dir("workflows")` - do not hardcode a repo-root
`workflows/` path (it does not exist).

## Pushing CI workflow files
The checkout's GitHub App token lacks the `workflows` permission, so git pushes that
add/modify `.github/workflows/*` are rejected by GitHub. The `GH_TOKEN` env var has the
`workflow` scope: clear the global `http.https://github.com/.extraheader` extraheader,
run `gh auth setup-git`, push, then restore the saved gitconfig.

## Github workflow
Under `.github/workflows/` the `nebuko-workflow.yaml` is build to be use externally.
So by contention all repo's workflow related file are prefixed with `internal-`.

## Known pre-existing test failures (not regressions)
`uv run pytest src/nebuko/engine/lib` has **15 failures on `main`** unrelated to
config/resolver work — all in workflow-routing and real-workflow-YAML tests
(`test_workflow.py`, `test_resolve_workflow_step.py`, `test_resolve_config.py`,
`build_output/test_build_output.py`, `resolve_route`). They stem from the
checkout carrying both a root `workflows/` dir and `defaults/workflows/`. The
`tests/` suite (616) and the config/agent_script modules are green, so diff
against this baseline before blaming your change.

## Thinking/reasoning config plumbing (issue #941)
`thinking_enabled`/`preserve_thinking`/`reasoning_effort` live on `LLMConfig`
and (since #941) on `AgentComplexityOverride` too — precedence tier > llm, `None`
inherits; that model now uses `extra="forbid"`. `resolve_llm_config.py` owns the
top-level SDK `reasoning_effort`: the OpenHands SDK defaults it to `"high"` and
emits it for models that support the param, so when thinking resolves off the
resolver must send **an explicit `reasoning_effort=None`** (omitting the key
lets the SDK default stand). Verify end-to-end with
`select_chat_options(LLM(**cfg), {}, has_tools=True)`.

## Git checkpoints in this sandbox (rebase gotchas)
The workspace checkout is a **shallow/grafted** clone, so `git rev-list
--left-right --count origin/main...HEAD` reports `0 0` until history is
deepened, and `git rebase origin/main` aborts with "empty ident name" until an
identity is set. Use:
```bash
git config user.email "agent@nebuko.local" && git config user.name "Nebuko Agent"
git fetch --unshallow origin || git fetch --depth=200 origin
git rebase origin/main          # resolve any conflicts, then --continue
git push --force-with-lease origin <branch>
```
A leftover `.git/rebase-merge` from a failed attempt blocks the next rebase:
`git rebase --abort` (or `rm -fr .git/rebase-merge`) before retrying.

## System prompt assembly (issue #943)

The agent's **entire static system prompt** is the module constant
`_SYSTEM_PROMPT` in
`src/nebuko/engine/lib/agent_script/features/run_agent/run_agent.py`, passed as
the SDK's inline `Agent(system_prompt=...)` escape hatch. That escape hatch
bypasses the SDK prompt-section registry (`openhands/sdk/context/prompts/`)
entirely: the built-in OpenHands persona, the coding-workflow sections and
`<MEMORY>` / `<SECURITY>` / `<SECURITY_RISK_ASSESSMENT>` / `<EXTERNAL_SERVICES>` /
`<PROCESS_MANAGEMENT>` / model-specific notes no longer render. Do **not**
reintroduce `persona`, `system_prompt_kwargs` or `security_policy_filename` — all
three only fed the registry and are now silent no-ops (a test pins this).

Useful facts when touching this area:

- **Three SDK blocks are unremovable by field:** `memory`, `external_services`
  and `process_management` all `guard -> True`; only `security`
  (`security_policy_filename`) and `security_risk_assessment`
  (`llm_security_analyzer`) have guard flags. Hence the inline prompt.
- The SDK **appends each loaded tool's `prompt_guidance`** after an inline
  `system_prompt` (`agent/base.py`), so tool guidance survives.
- **Two tiers.** The static prompt above; the *dynamic* tier carries nebuko's
  `SHARED_EXIT_RULES` + "Your Team" (`AgentContext(system_message_suffix=...)`,
  `resolve_config/features/team_context/`) plus datetime and secrets. The literal
  label `Dynamic Context:` is SDK console-visualization only — the model never
  sees it. The static prompt's closing line defers to that tier for the
  `dispatch_next` / `AGENT_RUN_CONFIG` contract, so keep the dynamic tier.
- **Skills reach the prompt in one of two modes** (issue #955, which closed the
  `SKILL_CONTEXT_FILE` -> `security_policy_filename` channel orphaned when PR
  #770 deleted its producer, and the SDK-routing work of #570). `inject` entries
  have their full body rendered by `run_loop.build_prompt` under a
  `## Injected skills` block (position: role prompt -> step prompt -> injected
  skills -> inputs -> user request -> context; it survives `prompt_replace`).
  `discover` entries become SDK `Skill` objects on
  `AgentContext(skills=[...])` in `run_agent._build_agent_context`, which renders
  the name + `description` catalog (`<available_skills>`) and keeps the body out
  of the prompt until the agent calls the SDK's `invoke_skill` tool — attached
  explicitly, because the SDK's auto-attach rule only fires for AgentSkills-format
  skills. Both modes resolve through one value object,
  `resolve_config/features/skill_context/skill_context.py`; the entry grammar
  (`SkillSpec`) is shared with the linter's C5 rule.
