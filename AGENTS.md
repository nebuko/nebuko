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

## Workflow resolution precedence
Workflow files resolve the repo-local override `.nebuko/workflows/<name>`
first, then the shipped `defaults/workflows/<name>`. A repo-root `workflows/`
directory is **never** consulted — untracked scaffolding there cannot shadow
shipped defaults. To override a shipped workflow in a checkout, place a file
with the same filename under `.nebuko/workflows/` (or set `WORKFLOWS_DIR` /
`LOCAL_WORKFLOWS_DIR` as documented in `default_paths.py`).
`uv run pytest tests src/nebuko/engine/lib` is fully green; red means a real
regression — no baseline-diffing required.

## Thinking/reasoning config plumbing (issue #941)
`thinking_enabled`/`preserve_thinking`/`reasoning_effort` live on `LLMConfig`
and (since #941) on `AgentComplexityOverride` too — precedence tier > llm, `None`
inherits; that model now uses `extra="forbid"`. `resolve_llm_config.py` owns the
top-level SDK `reasoning_effort`: the OpenHands SDK defaults it to `"high"` and
emits it for models that support the param, so when thinking resolves off the
resolver must send **the explicit string `"none"`** — omitting the key lets the
SDK default stand, and sending `None` sends nothing, which the provider reads as
its own default (reasoning on, issue #958). The SDK's model-feature gate
(`supports_reasoning_effort`) keeps `"none"` off the wire for models that do not
accept the param. Verify end-to-end with
`select_chat_options(LLM(**cfg), {}, has_tools=True)`.

## Git checkpoints in this sandbox (merge procedure)
The workspace checkout is a **shallow/grafted** clone, so `git rev-list
--left-right --count origin/main...HEAD` reports `0 0` until history is
deepened, and git aborts with "empty ident name" until an identity is set.

### Usage
```bash
git config user.email "agent@nebuko.local" && git config user.name "Nebuko Agent"
git fetch --unshallow origin || git fetch --depth=200 origin
git checkout <branch>
git merge origin/main
git push origin <branch>        # plain push — never --force-with-lease
```

### Conflict behaviour
The engine merges `origin/<base>` into the branch; on a conflict it aborts the
merge (leaving a clean worktree) and reports the conflicted paths in the
"Branch conflict — fix first" prompt section. You then resolve by re-running
the merge yourself: `git merge origin/main`, resolve the markers, `git add` +
`git commit`, then push plainly. A merge never rewrites published history,
so no force-push is ever needed.

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
  `dispatch_next` / `call_agent` contract, so keep the dynamic tier.
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
