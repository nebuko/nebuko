"""Resolve one agent step's injectable / discoverable skill attachment (issue #955).

Injectable skills are rendered into the assembled prompt; discoverable ones are
exposed to the agent as a name + summary catalog it reads in full on demand
through the SDK's ``invoke_skill`` tool. Both consumers of a step (the prompt
builder and the agent context builder) resolve through this one object, so the
two modes can never disagree about which skills a step carries.

The resolver hands the inputs over the flat ``KEY=value`` environment contract:
the agent-level and step-level specs as JSON, plus the absolute skills
directory, so both processes resolve identically regardless of their cwd.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

from nebuko.engine.lib.config.sub_domains.skill_spec.skill_spec import INJECT, SkillSpec

#: Resolver output keys carrying this step's skill inputs.
AGENT_SKILL_SPECS_ENV = "AGENT_SKILL_SPECS"
STEP_SKILL_SPECS_ENV = "STEP_SKILL_SPECS"
SKILLS_DIR_ENV = "SKILLS_DIR"

_INJECTED_HEADER = "## Injected skills"
_SUMMARY_MAX_LENGTH = 1024


@dataclass(frozen=True)
class SkillDoc:
    """One resolved skill file: identity, path, body, and its summary line."""

    name: str
    path: str
    content: str
    summary: str


@dataclass(frozen=True)
class SkillContext:
    """Effective skill attachment for one agent step."""

    injected: tuple[SkillDoc, ...] = ()
    discoverable: tuple[SkillDoc, ...] = ()

    @classmethod
    def resolve(
        cls,
        agent_specs: Optional[Sequence[SkillSpec]] = None,
        step_specs: Optional[Sequence[SkillSpec]] = None,
        *,
        skills_dir: Optional[str] = None,
    ) -> "SkillContext":
        """Merge the agent's and the step's entries into one attachment.

        The step is the narrower scope, so its mode wins when a name appears at
        both levels. Order is deterministic: the agent's names first, then the
        step-only ones. A name whose file cannot be read is dropped — a missing
        skill is a lint error (rule C5), not a runtime failure.
        """
        modes: dict[str, str] = {}
        for spec in agent_specs or ():
            for name in spec.names:
                modes[name] = spec.mode
        for spec in step_specs or ():
            for name in spec.names:
                modes[name] = spec.mode

        injected: list[SkillDoc] = []
        discoverable: list[SkillDoc] = []
        for name, mode in modes.items():
            doc = _load_doc(name, skills_dir)
            if doc is None:
                continue
            (injected if mode == INJECT else discoverable).append(doc)
        return cls(injected=tuple(injected), discoverable=tuple(discoverable))

    def render_injected(self) -> str:
        """The full bodies of the inject-mode skills, or ``""`` when none."""
        if not self.injected:
            return ""
        blocks = [
            f"### {doc.name}\n\n{doc.content.strip()}" for doc in self.injected
        ]
        return "\n\n".join([_INJECTED_HEADER, *blocks])

    def to_sdk_skills(self) -> list[Any]:
        """SDK ``Skill`` objects for ``AgentContext(skills=...)``.

        Marked AgentSkills-format so the SDK lists them in ``<available_skills>``
        (name + description only) instead of auto-injecting their whole body,
        which is how progressive disclosure is expressed on this side.
        """
        from openhands.sdk.skills.skill import Skill

        return [
            Skill(
                name=doc.name,
                content=doc.content,
                description=doc.summary,
                source=doc.path,
                is_agentskills_format=True,
            )
            for doc in self.discoverable
        ]

    @property
    def needs_invoke_tool(self) -> bool:
        """Whether the run needs the SDK's ``invoke_skill`` retrieval tool."""
        return bool(self.discoverable)


def resolve_skill_path(name: str, skills_dir: Optional[str] = None) -> str:
    """Resolve a skill entry to its file path.

    Bare names live at ``<skills_dir>/<name>.md``. A path-like reference (the
    ``tmp_impl/skills/step_1`` a plan workflow generates) is tried as written
    relative to the checkout first, then through the skills directory. Mirrors
    the candidate order the linter uses, so the two agree on what exists.
    """
    candidates = skill_path_candidates(name, skills_dir)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return str(candidates[-1])


def skill_path_candidates(
    name: str, skills_dir: Optional[str] = None
) -> list[Path]:
    """Candidate paths for ``name``, most specific first."""
    base = Path(skills_dir) if skills_dir else Path(_default_skills_dir())
    checked = [name] if name.endswith(".md") else [name, f"{name}.md"]
    if "/" in name or os.sep in name:
        roots = [Path.cwd()]
        repo_root = os.environ.get("GFA_REPO_ROOT")
        if repo_root:
            roots.append(Path(repo_root))
        candidates = [root / candidate for root in roots for candidate in checked]
    else:
        candidates = []
    return candidates + [base / candidate for candidate in checked]


def specs_to_env(specs: Sequence[SkillSpec]) -> str:
    """Serialize skill specs for the flat ``KEY=value`` hand-off."""
    if not specs:
        return ""
    return json.dumps(
        [spec.model_dump() for spec in specs], separators=(",", ":")
    )


def specs_from_env(raw: Optional[str]) -> list[SkillSpec]:
    """Parse the specs ``specs_to_env`` emitted; empty on a malformed payload."""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    return SkillSpec.parse_all(payload)


def skill_context_from_env(env: Mapping[str, str]) -> SkillContext:
    """Resolve this step's attachment from the resolver's environment payload."""
    return SkillContext.resolve(
        specs_from_env(env.get(AGENT_SKILL_SPECS_ENV)),
        specs_from_env(env.get(STEP_SKILL_SPECS_ENV)),
        skills_dir=env.get(SKILLS_DIR_ENV) or None,
    )


def _default_skills_dir() -> str:
    import nebuko.engine.lib.default_paths

    return default_paths.config_dir("skills", "SKILLS_DIR")


def _load_doc(name: str, skills_dir: Optional[str]) -> Optional[SkillDoc]:
    path = Path(resolve_skill_path(name, skills_dir))
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8")
    _, body = _split_frontmatter(raw)
    return SkillDoc(
        name=name, path=str(path), content=body, summary=_summarize(raw)
    )


def _summarize(content: str) -> str:
    """The skill's one-line summary: ``description`` frontmatter, else first line.

    The fallback mirrors the SDK's own catalog rule (first non-empty, non-``#``
    line) so a skill without frontmatter still gets a usable summary.
    """
    front, body = _split_frontmatter(content)
    description = front.get("description")
    if isinstance(description, str) and description.strip():
        return _truncate(description.strip())
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return _truncate(stripped)
    return ""


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a leading ``---`` YAML block from the skill body."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    try:
        data = yaml.safe_load(content[4:end])
    except yaml.YAMLError:
        return {}, content[end + 4:]
    return (data if isinstance(data, dict) else {}), content[end + 4:]


def _truncate(text: str) -> str:
    return text[:_SUMMARY_MAX_LENGTH]
