"""Offline, config-level linter for the layered agent-config YAML (issue #774).

The Pydantic loader (``engine/lib/config/features/load_config``) enforces the
*field-level* shape of one agent config — types, required keys, enums — but only
after ``${layer.key}`` references have been resolved. It therefore cannot report
a dangling reference (``resolve_refs`` raises a bare ``KeyError`` before the
loader ever runs), a tool name the runtime silently skips, a ``prompt_file`` or
skill that does not exist, or a prunable dead entry. Those defects surface today
only at run time, mid-run.

This module owns the offline pass that reports them all at once, mirroring the
workflow linter (``nebuko.engine.lint``): a pure, read-only rule engine that
reuses ``LintIssue`` and the ``REF_PATTERN`` grammar from the config package.

Prompt and skill references are resolved through the same layered lookup the
engine uses at run time (:class:`nebuko.engine.paths.NebukoPaths`: the repo's
``.nebuko/`` overrides first, the shipped ``defaults/`` tree second), so a
config that ships with Nebuko is never falsely flagged for a file that the
runtime can find.
"""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from nebuko.engine.lint import LintIssue

__all__ = ["ConfigLinter", "KNOWN_TOOLS", "RULE_SEVERITY"]

_SEVERITY_ERROR = "error"
_SEVERITY_WARNING = "warning"

# Stable rule identifiers (issue #774). C6 is the only warning; everything else
# is an error (promoted from warnings under ``--strict``).
_RULE_SCHEMA = "C1-schema"
_RULE_DANGLING_REF = "C2-dangling-ref"
_RULE_UNKNOWN_TOOL = "C3-unknown-tool"
_RULE_PROMPT_FILE = "C4-prompt-file"
_RULE_SKILL_FILE = "C5-skill-file"
_RULE_UNREFERENCED = "C6-unreferenced-entry"
_RULE_MCP_SERVERS = "C7-mcp-servers"
_RULE_MISSING_ROLE = "C8-missing-role"

RULE_SEVERITY: dict[str, str] = {
    _RULE_SCHEMA: _SEVERITY_ERROR,
    _RULE_DANGLING_REF: _SEVERITY_ERROR,
    _RULE_UNKNOWN_TOOL: _SEVERITY_ERROR,
    _RULE_PROMPT_FILE: _SEVERITY_ERROR,
    _RULE_SKILL_FILE: _SEVERITY_ERROR,
    _RULE_UNREFERENCED: _SEVERITY_WARNING,
    _RULE_MCP_SERVERS: _SEVERITY_ERROR,
    _RULE_MISSING_ROLE: _SEVERITY_WARNING,
}

# Canonical tool names accepted at run time. Source of truth:
# engine/lib/agent_script/features/run_agent/run_agent.py (sdk_tool_names ∪
# builtin_tool_map ∪ {finalize, report_scaffold_branch, complexity_review});
# duplicated here so the pure linter need not import the SDK runtime.
KNOWN_TOOLS: frozenset[str] = frozenset(
    {
        "terminal",
        "terminal_batch",
        "file_editor",
        "task_tracker",
        "read",
        "edit",
        "write",
        "think",
        "finish",
        "finalize",
        "report_scaffold_branch",
        "complexity_review",
        "fetch_comment_bodies",
    }
)

# Sentinel returned by ref lookup when a dotted path does not resolve.
_MISSING = object()
# Stand-in substituted for a broken ref before Pydantic validation, so an
# unresolvable reference is reported by C2 instead of crashing C1.
_PLACEHOLDER = "__lint_placeholder__"

# Mirrors ``MCPServerConfig._ENV_VAR_NAME``: an MCP ``env`` entry names the OS
# env var holding a secret, never the secret itself. Duplicated here so the pure
# linter need not import the config package's Pydantic entities.
_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_LAYER_NOUNS = (("llms", "llm"), ("providers", "provider"), ("mcp_servers", "mcp server"))

# The root section holding reusable MCP server specs; agents reference its
# entries as ``"${mcp_servers.<name>}"``.
_MCP_LAYER = "mcp_servers"

# The config sub-domains the linter needs, imported lazily: the ``config``
# package only becomes importable once ``ensure_import_path()`` has run.
_APP_CONFIG: Any = None
_REF_PATTERN: Any = None
_SKILL_SPEC: Any = None


def _ensure_runtime() -> None:
    """Make the legacy ``config`` package importable and cache its symbols."""
    global _APP_CONFIG, _REF_PATTERN, _SKILL_SPEC
    if _REF_PATTERN is not None:
        return
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from config import AppConfig, REF_PATTERN, SkillSpec

    _APP_CONFIG = AppConfig
    _REF_PATTERN = REF_PATTERN
    _SKILL_SPEC = SkillSpec


def _is_url(value: str) -> bool:
    """True for a ``scheme://netloc`` prompt location (mirrors ``prompt.is_url``).

    The runtime prompt loader accepts URLs as well as file paths, so a URL
    ``prompt_file`` is not a missing file.
    """
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def _iter_strings(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(key_path, value)`` for every string in a nested mapping/list."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _iter_strings(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_strings(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def _iter_agents(raw: dict) -> Iterator[tuple[str, dict]]:
    """Yield ``(name, mapping)`` for every well-formed entry in ``agents``."""
    agents = raw.get("agents")
    if not isinstance(agents, dict):
        return
    for name, agent in agents.items():
        if isinstance(agent, dict):
            yield str(name), agent


def _mcp_io_issues(entry: dict, location: str) -> list[LintIssue]:
    """C7: validate ``args`` and ``env`` shapes for one MCP server entry."""
    issues: list[LintIssue] = []
    args = entry.get("args")
    if args is not None and not (
        isinstance(args, list) and all(isinstance(arg, str) for arg in args)
    ):
        issues.append(
            _make_issue(
                _RULE_MCP_SERVERS,
                "mcp server args must be a list of strings",
                f"{location}.args",
                "Use a YAML list of command-line arguments",
            )
        )
    env = entry.get("env")
    if env is None:
        return issues
    if not isinstance(env, dict):
        issues.append(
            _make_issue(
                _RULE_MCP_SERVERS,
                "mcp server env must map child var -> OS env var name",
                f"{location}.env",
                "See docs/configuration.md",
            )
        )
        return issues
    for child_var, source_name in env.items():
        if not (
            isinstance(child_var, str)
            and _ENV_VAR_NAME.match(child_var)
            and isinstance(source_name, str)
            and _ENV_VAR_NAME.match(source_name)
        ):
            issues.append(
                _make_issue(
                    _RULE_MCP_SERVERS,
                    "mcp server env values must be environment variable names, "
                    "not literal secrets",
                    f"{location}.env.{child_var}",
                    "Set the value in the OS env / CI secrets and reference its name",
                )
            )
    return issues


def _mcp_server_issues(entry: Any, location: str) -> list[LintIssue]:
    """C7: report every field-shape problem in one MCP server entry at once."""
    if not isinstance(entry, dict):
        return [
            _make_issue(
                _RULE_MCP_SERVERS,
                "mcp server entry must be a mapping",
                location,
                "Use keys name, command, args, env",
            )
        ]
    issues: list[LintIssue] = []
    for field in ("name", "command"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(
                _make_issue(
                    _RULE_MCP_SERVERS,
                    f"mcp server {field} must be a non-empty string",
                    f"{location}.{field}",
                    "See docs/configuration.md",
                )
            )
    issues.extend(_mcp_io_issues(entry, location))
    transport = entry.get("transport")
    if transport is not None and transport != "stdio":
        issues.append(
            _make_issue(
                _RULE_MCP_SERVERS,
                f"unsupported mcp transport '{transport}'",
                f"{location}.transport",
                "Only 'stdio' is supported",
            )
        )
    return issues


def _catalog_ref_name(value: Any) -> str | None:
    """Return the catalog key when ``value`` is a full ``${mcp_servers.<key>}`` ref."""
    if not isinstance(value, str):
        return None
    match = _REF_PATTERN.fullmatch(value)
    if match is None:
        return None
    parts = match.group(1).split(".")
    if len(parts) == 2 and parts[0] == _MCP_LAYER:
        return parts[1]
    return None


def _catalog_issues(key: Any, entry: Any) -> list[LintIssue]:
    """C7: validate one root-catalog entry at ``mcp_servers.<key>``."""
    location = f"{_MCP_LAYER}.{key}"
    issues = _mcp_server_issues(entry, location)
    if isinstance(entry, dict) and entry.get("name") != key:
        issues.append(
            _make_issue(
                _RULE_MCP_SERVERS,
                f"mcp_servers catalog key '{key}' must equal the server's name "
                f"'{entry.get('name')}'",
                f"{location}.name",
                "Use the same string for the catalog key and the server name",
            )
        )
    return issues


def _lookup(root: Any, dotted: str) -> Any:
    """Resolve a dotted path against ``root``; return ``_MISSING`` if absent."""
    obj = root
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return _MISSING
        obj = obj[part]
    return obj


def _iter_refs(node: Any) -> Iterator[str]:
    """Yield every ``${ref}`` dotted path found anywhere in ``node``."""
    for _, value in _iter_strings(node):
        for match in _REF_PATTERN.finditer(value):
            yield match.group(1)


def _inline_substitute(match: Any, root: Any) -> str:
    """String substitution for a partial ref; placeholder when unresolved."""
    target = _lookup(root, match.group(1))
    if target is _MISSING:
        return _PLACEHOLDER
    return str(target) if not isinstance(target, (dict, list)) else match.group(0)


def _resolve_for_schema(node: Any, root: Any) -> Any:
    """Resolve refs best-effort; broken refs become a placeholder (never raise)."""
    if isinstance(node, dict):
        return {k: _resolve_for_schema(v, root) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_for_schema(item, root) for item in node]
    if isinstance(node, str):
        full = _REF_PATTERN.fullmatch(node)
        if full:
            target = _lookup(root, full.group(1))
            if target is _MISSING:
                return _PLACEHOLDER
            return _resolve_for_schema(target, root)
        return _REF_PATTERN.sub(lambda m: _inline_substitute(m, root), node)
    return node


def _prompt_candidates(
    root: Path, prompt_dirs: tuple[Path, ...], prompt_file: str
) -> Iterator[Path]:
    """Candidate paths for a ``prompt_file``, explicit path first.

    The runtime keeps only the file *name* of a configured prompt and re-resolves
    it through the layered prompt lookup (``run_loop._apply_prompt_layer``), so
    the shipped ``.agents/prompts/{agent}.md`` convention resolves to
    ``defaults/prompts/{agent}.md``.
    """
    yield root / prompt_file
    name = os.path.basename(prompt_file)
    for directory in prompt_dirs:
        yield directory / name


def _skill_candidates(
    root: Path, skill_dirs: tuple[Path, ...], skill: str
) -> Iterator[Path]:
    """Candidate paths for a ``skills`` entry, explicit path first.

    ``skills: ["x"]`` may name a bare skill (``skills/x.md``) or an explicit
    path; a bare name is also looked up in the layered skill directories.
    """
    yield root / skill
    if skill.endswith(".md") or "/" in skill:
        for directory in skill_dirs:
            yield directory / skill
        return
    yield root / f"skills/{skill}.md"
    for directory in skill_dirs:
        yield directory / f"{skill}.md"


def _first_existing(candidates: Iterator[Path]) -> bool:
    """True as soon as one candidate exists on disk."""
    return any(candidate.is_file() for candidate in candidates)


def _promote_to_error(issue: LintIssue) -> LintIssue:
    """Return ``issue`` with its severity raised to ``error`` (strict mode)."""
    if issue.severity == _SEVERITY_WARNING:
        return dataclasses.replace(issue, severity=_SEVERITY_ERROR)
    return issue


def _make_issue(rule: str, message: str, location: str, hint: str) -> LintIssue:
    """Build a config-scoped issue (``step`` is always empty for config rules)."""
    return LintIssue(
        rule=rule,
        severity=RULE_SEVERITY[rule],
        message=message,
        step="",
        location=location,
        hint=hint,
    )


def _referenced_layers(raw: dict) -> dict[str, set[str]]:
    """Collect the ``llms``/``providers``/``mcp_servers`` entries reached by a ref."""
    referenced: dict[str, set[str]] = {layer: set() for layer, _ in _LAYER_NOUNS}
    for ref in _iter_refs(raw):
        parts = ref.split(".")
        if len(parts) >= 2 and parts[0] in referenced:
            referenced[parts[0]].add(parts[1])
    return referenced


class ConfigLinter:
    """Check an agent-config definition against the config-level invariants.

    The linter loads the YAML *without* resolving references, so it can report a
    dangling ``${ref}`` instead of crashing, then runs every rule against the raw
    mapping. It never mutates, resolves, or loads anything at run time.
    ``strict`` promotes warnings to errors so a CI consumer can gate on them.

    ``root`` is the base directory for the explicit prompt/skill paths written in
    the config (default: the config file's own directory). ``prompt_dirs`` and
    ``skill_dirs`` are the layered lookup directories; both default to the
    ``.nebuko/`` → ``defaults/`` layering the engine uses.
    """

    def __init__(
        self,
        strict: bool = False,
        root: Path | None = None,
        prompt_dirs: tuple[Path, ...] | None = None,
        skill_dirs: tuple[Path, ...] | None = None,
    ) -> None:
        """Store the strict flag and the file-lookup roots."""
        self._strict = strict
        self._root = root
        self._prompt_dirs = prompt_dirs
        self._skill_dirs = skill_dirs

    def lint(self, path: Path) -> list[LintIssue]:
        """Load ``path`` and return every violated invariant, in rule order."""
        _ensure_runtime()
        from nebuko.engine.paths import ConfigKind

        raw = self._load_raw(path)
        root = self._root if self._root is not None else path.parent
        prompt_dirs = self._resolve_dirs(ConfigKind.PROMPT, self._prompt_dirs)
        skill_dirs = self._resolve_dirs(ConfigKind.SKILL, self._skill_dirs)
        issues: list[LintIssue] = []
        issues.extend(self._check_refs(raw))
        issues.extend(self._check_schema(raw))
        issues.extend(self._check_tools(raw))
        issues.extend(self._check_roles(raw))
        issues.extend(self._check_mcp_servers(raw))
        issues.extend(self._check_prompt_files(raw, root, prompt_dirs))
        issues.extend(self._check_skill_files(raw, root, skill_dirs))
        issues.extend(self._check_unreferenced(raw))
        if self._strict:
            return [_promote_to_error(issue) for issue in issues]
        return issues

    def lint_all(self, paths: list[Path]) -> list[LintIssue]:
        """Run :meth:`lint` over every path and concatenate the issues."""
        issues: list[LintIssue] = []
        for path in paths:
            issues.extend(self.lint(path))
        return issues

    def _resolve_dirs(
        self, kind: "ConfigKind", override: tuple[Path, ...] | None
    ) -> tuple[Path, ...]:
        """Layered directories for ``kind``, unless the caller pinned them."""
        if override is not None:
            return override
        from nebuko.engine.paths import NebukoPaths, repo_root

        paths = NebukoPaths.discover(repo_root())
        return tuple(paths.search_roots(kind))

    def _load_raw(self, path: Path) -> dict:
        """Parse ``path`` as a YAML mapping (raises ``ValueError`` otherwise)."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(
                f"Config file must contain a YAML mapping, got {type(raw).__name__}"
            )
        return raw

    def _check_refs(self, raw: dict) -> list[LintIssue]:
        """C2: every ``${layer.key}`` must resolve against the raw config."""
        issues: list[LintIssue] = []
        for location, value in _iter_strings(raw):
            for match in _REF_PATTERN.finditer(value):
                ref = match.group(1)
                if _lookup(raw, ref) is _MISSING:
                    issues.append(
                        _make_issue(
                            _RULE_DANGLING_REF,
                            f"undefined ref ${{{ref}}}",
                            location,
                            "Define the referenced entry or fix the ref",
                        )
                    )
        return issues

    def _check_schema(self, raw: dict) -> list[LintIssue]:
        """C1: the resolved config must validate against the Pydantic schema."""
        resolved = _resolve_for_schema(raw, raw)
        try:
            _APP_CONFIG.model_validate(resolved)
        except ValidationError as exc:
            return [
                _make_issue(
                    _RULE_SCHEMA,
                    detail["msg"],
                    ".".join(str(part) for part in detail["loc"]),
                    "See docs/configuration.md",
                )
                for detail in exc.errors()
            ]
        return []

    def _check_tools(self, raw: dict) -> list[LintIssue]:
        """C3: every ``tools`` entry must be a known tool name."""
        issues: list[LintIssue] = []
        known = ", ".join(sorted(KNOWN_TOOLS))
        for name, agent in _iter_agents(raw):
            for tool in agent.get("tools") or []:
                if tool not in KNOWN_TOOLS:
                    issues.append(
                        _make_issue(
                            _RULE_UNKNOWN_TOOL,
                            f"unknown tool '{tool}'",
                            f"agents.{name}.tools",
                            f"Use one of: {known}",
                        )
                    )
        return issues

    def _check_roles(self, raw: dict) -> list[LintIssue]:
        """C8: every agent should declare a one-line ``role``.

        Warning, not error: ``role`` feeds the injected "Your Team" section
        (issue #476), so a missing one degrades team awareness but never breaks
        a run.
        """
        issues: list[LintIssue] = []
        for name, agent in _iter_agents(raw):
            role = agent.get("role")
            if not isinstance(role, str) or not role.strip():
                issues.append(
                    _make_issue(
                        _RULE_MISSING_ROLE,
                        f"agent '{name}' has no role",
                        f"agents.{name}.role",
                        "Add a one-line role describing what this agent does",
                    )
                )
        return issues

    def _check_mcp_servers(self, raw: dict) -> list[LintIssue]:
        """C7: validate the root catalog and every agent-level ``mcp_servers`` list."""
        issues = self._check_mcp_catalog(raw)
        for name, agent in _iter_agents(raw):
            servers = agent.get("mcp_servers")
            if servers is None:
                continue
            if not isinstance(servers, list):
                issues.append(
                    _make_issue(
                        _RULE_MCP_SERVERS,
                        "mcp_servers must be a list",
                        f"agents.{name}.mcp_servers",
                        "Use a YAML list of server entries",
                    )
                )
                continue
            issues.extend(self._check_agent_mcp_list(name, servers))
        return issues

    def _check_mcp_catalog(self, raw: dict) -> list[LintIssue]:
        """C7: every root ``mcp_servers`` catalog entry must be a well-formed spec."""
        catalog = raw.get(_MCP_LAYER)
        if catalog is None:
            return []
        if not isinstance(catalog, dict):
            return [
                _make_issue(
                    _RULE_MCP_SERVERS,
                    "mcp_servers must be a mapping of name -> server spec",
                    _MCP_LAYER,
                    "Use a YAML mapping keyed by server name",
                )
            ]
        issues: list[LintIssue] = []
        for key, entry in catalog.items():
            issues.extend(_catalog_issues(key, entry))
        return issues

    def _check_agent_mcp_list(self, name: str, servers: list) -> list[LintIssue]:
        """C7: every agent-level entry is an inline spec or a catalog ref.

        A catalog ref is validated once at its catalog location (see
        :meth:`_check_mcp_catalog`); a dangling ref is left to C2. Here we only
        reject an entry that is neither a well-formed inline spec nor a ref.
        """
        issues: list[LintIssue] = []
        seen: set[str] = set()
        for index, entry in enumerate(servers):
            location = f"agents.{name}.mcp_servers[{index}]"
            ref = _catalog_ref_name(entry)
            if ref is not None:
                server_name = ref
            else:
                issues.extend(_mcp_server_issues(entry, location))
                server_name = entry.get("name") if isinstance(entry, dict) else None
            if isinstance(server_name, str):
                if server_name in seen:
                    issues.append(
                        _make_issue(
                            _RULE_MCP_SERVERS,
                            f"duplicate mcp server name '{server_name}'",
                            f"{location}.name",
                            "Give each server a unique name",
                        )
                    )
                seen.add(server_name)
        return issues

    def _check_prompt_files(
        self, raw: dict, root: Path, prompt_dirs: tuple[Path, ...]
    ) -> list[LintIssue]:
        """C4: every ``prompt_file`` must resolve to an existing file or URL."""
        issues: list[LintIssue] = []
        for name, agent in _iter_agents(raw):
            prompt_file = agent.get("prompt_file")
            if not isinstance(prompt_file, str) or _is_url(prompt_file):
                continue
            candidates = _prompt_candidates(root, prompt_dirs, prompt_file)
            if not _first_existing(candidates):
                issues.append(
                    _make_issue(
                        _RULE_PROMPT_FILE,
                        f"prompt file '{prompt_file}' does not exist",
                        f"agents.{name}.prompt_file",
                        "Create the prompt file "
                        "(convention: .agents/prompts/{agent}.md)",
                    )
                )
        return issues

    def _check_skill_files(
        self, raw: dict, root: Path, skill_dirs: tuple[Path, ...]
    ) -> list[LintIssue]:
        """C5: every ``skills`` entry must resolve to a matching skill file.

        Entries are walked through the shared :class:`SkillSpec` grammar, so the
        mode-object shape (``- inject: [x]`` / ``- discover: [y]``) is validated
        too — an unknown entry shape yields no names and is reported by C1.
        """
        issues: list[LintIssue] = []
        for name, agent in _iter_agents(raw):
            for skill in _SKILL_SPEC.iter_names(agent.get("skills")):
                candidates = _skill_candidates(root, skill_dirs, skill)
                if not _first_existing(candidates):
                    issues.append(
                        _make_issue(
                            _RULE_SKILL_FILE,
                            f"missing skill file for '{skill}'",
                            f"agents.{name}.skills",
                            f"Create skills/{skill}.md or remove the skill",
                        )
                    )
        return issues

    def _check_unreferenced(self, raw: dict) -> list[LintIssue]:
        """C6: ``llms``/``providers``/``mcp_servers`` entries nothing references are dead config."""
        referenced = _referenced_layers(raw)
        issues: list[LintIssue] = []
        for layer, noun in _LAYER_NOUNS:
            entries = raw.get(layer)
            if not isinstance(entries, dict):
                continue
            for key in entries:
                if key not in referenced[layer]:
                    issues.append(
                        _make_issue(
                            _RULE_UNREFERENCED,
                            f"{noun} '{key}' is never referenced by any agent",
                            f"{layer}.{key}",
                            "Remove the unused entry or reference it",
                        )
                    )
        return issues
