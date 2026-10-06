# Step 1 / 4 — Agent-config lint rule engine (`src/nebuko/engine/config_lint.py`)

## Instructions

Goal: the pure rule engine that lints the layered agent config, mirroring
`WorkflowLinter` in `src/nebuko/engine/lint.py`.

Create `src/nebuko/engine/config_lint.py`. Reuse `LintIssue` from
`nebuko.engine.lint` unchanged (frozen dataclass: `rule, severity, message,
step, location, hint`). For config issues leave `step=""` and put the dotted
config key path (e.g. `agents.genius.tools`) in `location`. Reuse the
`_SEVERITY_ERROR = "error"` / `_SEVERITY_WARNING = "warning"` convention.

Define rule constants and a module-level `RULE_SEVERITY: dict[str, str]`:

- `C1-schema` (error), `C2-dangling-ref` (error), `C3-unknown-tool` (error),
  `C4-prompt-file` (error), `C5-skill-file` (error),
  `C6-unreferenced-entry` (warning).

`KNOWN_TOOLS: frozenset[str] = frozenset({"terminal", "file_editor",
"task_tracker", "think", "finish", "dispatch_next", "report_scaffold_branch"})`
— copied from `src/nebuko/engine/lib/agent_script/features/run_agent/run_agent.py`
(around lines 204–221: `sdk_tool_names` ∪ `builtin_tool_map` ∪
`{dispatch_next, report_scaffold_branch}`). Add a one-line comment naming that
site as the source of truth. Do **not** import `run_agent` — it drags SDK
runtime dependencies into a pure linter.

Class contract:

```python
class ConfigLinter:
    def __init__(self, strict: bool = False, root: Path | None = None) -> None: ...  # root defaults to the config file's directory at lint time
    def lint(self, path: Path) -> list[LintIssue]: ...
    def lint_all(self, paths: list[Path]) -> list[LintIssue]: ...
    def _load_raw(self, path: Path) -> dict: ...
    def _check_refs(self, raw: dict) -> list[LintIssue]: ...       # C2
    def _check_schema(self, raw: dict) -> list[LintIssue]: ...     # C1
    def _check_tools(self, raw: dict) -> list[LintIssue]: ...      # C3
    def _check_prompt_files(self, raw: dict) -> list[LintIssue]: ...  # C4
    def _check_skill_files(self, raw: dict) -> list[LintIssue]: ...   # C5
    def _check_unreferenced(self, raw: dict) -> list[LintIssue]: ...  # C6
```

`strict=True` promotes warnings to errors before returning — copy the exact
mechanism `WorkflowLinter` uses (see `src/nebuko/engine/lint.py`, the
`__init__`/`lint` pair).

Rule semantics — **all rules collect every finding per file; never stop at the
first**:

- **Load**: `yaml.safe_load` the file; its root must be a mapping, else raise
  `ValueError` (the CLI maps that to exit `2`). Let `yaml.YAMLError` propagate.
- **`C2-dangling-ref`**: recursively walk the raw mapping; for every string
  value find all `REF_PATTERN` matches (import `REF_PATTERN` from
  `nebuko.engine.lib.config` — the frozen config package made importable through
  `nebuko.engine.paths.ensure_import_path()`), resolve each dotted path
  segment-by-segment against the raw root; any missing segment → one error per
  occurrence: message `undefined ref ${...}`, `location` = key path of the
  offending string, hint to define the entry or fix the ref.
- **`C1-schema`**: build a best-effort-resolved copy (same walk as C2 but
  substitute a placeholder for broken refs so resolution cannot raise), then
  `AppConfig.model_validate(...)` (import `AppConfig` from the frozen config
  package). Catch `pydantic.ValidationError` and map each item of `e.errors()`
  to one issue: `location` = `".".join(str(p) for p in err["loc"])`, message =
  `err["msg"]`, hint pointing at `docs/configuration.md`. Pydantic already
  aggregates all errors — report them all.
- **`C3-unknown-tool`**: each `agents.<name>.tools` entry not in `KNOWN_TOOLS`
  → error at `agents.<name>.tools`, message `unknown tool '<t>'`, hint listing
  the known set (mirrors the runtime warning in `run_agent.py`).
- **`C4-prompt-file`**: each `agents.<name>.prompt_file` that resolves to no
  file → error. Accept a URL (the runtime prompt loader supports them) and
  resolve bare names through the layered prompt lookup (`.nebuko/prompts/` →
  `defaults/prompts/`) so a shipped config is never falsely flagged.
- **`C5-skill-file`**: each `agents.<name>.skills` entry `s`; accept
  `skills/{s}.md` (or the entry as written when it ends in `.md` or contains a
  slash), also through the layered skill lookup; if nothing resolves → error,
  hint to create the file or remove the skill.
- **`C6-unreferenced-entry`**: collect every `${llms.X}` / `${providers.Y}`
  occurrence anywhere in raw (including inside broken strings); each `llms` key
  never referenced → warning `llm '<x>' is never referenced by any agent` at
  `llms.<x>`; same for `providers` keys at `providers.<x>`.

`lint(path)` concatenates all `_check_*` results in rule order (C1…C6);
`lint_all` flattens over paths.

## Constraints

- Pure and offline: no network, no SDK runtime import, no mutation of the
  loaded mapping.
- File-reference checks are rooted at `root` (the config file's directory by
  default) plus the layered lookup — never at a hardcoded absolute path.
- Do not modify `src/nebuko/engine/lint.py`; reuse `LintIssue` as-is.

## Expected outcome

- `ConfigLinter().lint(<fixture>)` returns the expected `LintIssue` list for
  each of C1–C6, and `[]` for a fully valid config.
- Several findings of the same rule and across rules are all reported in one
  call.
- `ConfigLinter(strict=True)` turns a `C6` warning into an `error`.
- The module imports cleanly with only `pyyaml` + `pydantic` installed.

## State on this branch

The rule engine already landed on this branch. Treat this step as
verify-and-complete: run the checks below, fix any gap you find, and commit only
if something changed.

## Relevant files

- `src/nebuko/engine/lint.py` (`LintIssue`, `WorkflowLinter` — the pattern to mirror)
- `src/nebuko/engine/lib/config/` (frozen config package: `AppConfig`, `REF_PATTERN`, `resolve_refs`)
- `src/nebuko/engine/lib/config/features/resolve_refs/resolve_refs.py` (`REF_PATTERN`)
- `src/nebuko/engine/lib/agent_script/features/run_agent/run_agent.py` (canonical tool names)
- `src/nebuko/engine/paths.py` (layered lookup, `ensure_import_path`)
- `docs/configuration.md` (documented Provider/LLM/Agent schemas)

## Commit message

`[step 1/4] Agent-config lint rule engine (src/nebuko/engine/config_lint.py)`
