"""Materialize caller-declared secret slots into engine env vars (issue #834).

Adopters wire arbitrary MCP tokens (Brave, ...) without editing
``nebuko-workflow.yaml``: the reusable workflow declares nine generic slots,
each carried as a *pair* -- ``EXTRA_SECRET_n_NAME`` (an input: the env var name
the MCP expects) and ``EXTRA_SECRET_n_VALUE`` (a secret: its value). This
module pairs the slots into a reserved-safe env-var map and renders it for
``$GITHUB_ENV``, so the orchestration engine and the MCP servers it spawns see
the vars.

Secret *values* never travel through ``with:`` inputs: inputs land in
``github.event.inputs``, which the engine serializes into agent context. Only
the name does. See ``docs/adr/0008-mcp-secret-env-injection.md``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    "EXTRA_SECRET_SLOTS",
    "RESERVED_ENV_NAMES",
    "RESERVED_ENV_PREFIXES",
    "SecretEnvironment",
    "main",
]

#: Number of generic NAME/VALUE slots the reusable workflow declares.
EXTRA_SECRET_SLOTS: Final[int] = 9

#: Runner/engine internals a mapped name must never clobber.
RESERVED_ENV_NAMES: Final[frozenset[str]] = frozenset(
    {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "PATH",
        "HOME",
    }
)

#: Prefix families reserved for the runner and the Actions toolkit.
RESERVED_ENV_PREFIXES: Final[frozenset[str]] = frozenset({"GITHUB_", "ACTIONS_", "RUNNER_"})


def _is_reserved(name: str) -> bool:
    """Whether ``name`` collides with a runner/engine internal."""
    return name in RESERVED_ENV_NAMES or name.startswith(tuple(RESERVED_ENV_PREFIXES))


def _delimiter_for(value: str) -> str:
    """A heredoc delimiter guaranteed absent from ``value``."""
    index = 0
    while True:
        candidate = f"NEBUKO_SECRET_EOF_{index}"
        if candidate not in value:
            return candidate
        index += 1


@dataclass(frozen=True)
class SecretEnvironment:
    """Immutable env-var map materialized from the NAME/VALUE secret slots."""

    values: Mapping[str, str]

    @classmethod
    def from_slots(cls, env: Mapping[str, str]) -> "SecretEnvironment":
        """Pair ``EXTRA_SECRET_n_NAME``/``_VALUE`` slots (n=1..9) into env vars.

        Pairs with an empty name or value are skipped, as are reserved names.
        The first populated slot wins on a duplicate name, so a caller cannot
        silently shadow an earlier mapping by appending a later slot.
        """
        values: dict[str, str] = {}
        for index in range(1, EXTRA_SECRET_SLOTS + 1):
            name = (env.get(f"EXTRA_SECRET_{index}_NAME") or "").strip()
            value = env.get(f"EXTRA_SECRET_{index}_VALUE") or ""
            if not name or not value or _is_reserved(name) or name in values:
                continue
            values[name] = value
        return cls(values=values)

    def github_env_lines(self) -> tuple[str, ...]:
        """Render multiline-safe ``$GITHUB_ENV`` heredoc blocks for the values."""
        lines: list[str] = []
        for name, value in self.values.items():
            delimiter = _delimiter_for(value)
            lines.extend((f"{name}<<{delimiter}", value, delimiter))
        return tuple(lines)


def main() -> None:
    """Glue entry (``python -m nebuko.engine.lib.secret_env``).

    Reads the 18 slot vars from ``os.environ`` and appends the materialized
    values to the file at ``$GITHUB_ENV``. Logs names and counts only --
    never values.
    """
    secret_env = SecretEnvironment.from_slots(os.environ)
    github_env = os.environ.get("GITHUB_ENV", "")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as handle:
            for line in secret_env.github_env_lines():
                handle.write(f"{line}\n")
    names = ", ".join(secret_env.values) or "(none)"
    print(f"Materialized {len(secret_env.values)} secret env var(s): {names}")


if __name__ == "__main__":
    main()
