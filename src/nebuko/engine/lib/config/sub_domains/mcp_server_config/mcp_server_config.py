"""MCP server configuration entity."""

import re
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

# A POSIX environment-variable name: it identifies *where* a value lives, never
# the value itself. Anything else (a literal key, a path, a token) is rejected
# so a secret cannot be committed into the agent config.
_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MCPServerConfig(BaseModel):
    """One MCP server an agent's conversation attaches at launch."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    # Child process env var -> *name* of the OS env var holding the value.
    env: dict[str, str] = Field(default_factory=dict)
    transport: Literal["stdio"] = "stdio"

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        """Enforce the shape invariants the runtime wiring relies on."""
        if not self.name.strip():
            raise ValueError("name must be non-empty")
        if not self.command.strip():
            raise ValueError("command must be non-empty")
        for child_var, source_name in self.env.items():
            for label, value in (("key", child_var), ("value", source_name)):
                if not _ENV_VAR_NAME.match(value):
                    raise ValueError(
                        f"env {label} '{value}' must be an environment variable "
                        f"name, not a literal secret"
                    )
        return self
