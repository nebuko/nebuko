"""Provider configuration entity."""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# RFC 9110 ``tchar``: a legal HTTP header *name* is one or more of these.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# A JSON request-body parameter *name* (snake-style, e.g. ``prompt_cache_key``).
_BODY_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProviderConfig(BaseModel):
    """API endpoint, auth token env var, and static client-identity shape.

    The identity fields describe only the static, repo-level shape a provider
    expects — a client ``User-Agent``, the *names* of identity headers, and the
    *name* of a request-body parameter carrying the cache key. No per-issue
    value may enter config: a session identifier is engine-supplied at runtime
    (see ``resolve_provider_runtime``), never written here.
    """

    api_url: str
    api_token_env: str
    user_agent: Optional[str] = None
    session_header: Optional[str] = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # JSON-body param name carrying the engine-stamped issue id (e.g. Mistral's
    # ``prompt_cache_key``). The value rides ``litellm_extra_body``, never a
    # header (issue #1068).
    cache_key_param: Optional[str] = None
    # Some OpenAI-compatible gateways reject the message-level ``name`` field the
    # SDK attaches to tool-result messages ("[unsupported_parameter] ... 'name'
    # is not supported by this endpoint"). LiteLLM's ``drop_params`` only drops
    # top-level request params, not fields nested in messages, so providers that
    # need this must opt in explicitly (issue #881).
    drop_message_name: bool = False

    @field_validator("session_header")
    @classmethod
    def _validate_session_header(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _HEADER_NAME_RE.match(value):
            raise ValueError(f"session_header {value!r} is not a valid HTTP header name")
        return value

    @field_validator("cache_key_param")
    @classmethod
    def _validate_cache_key_param(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _BODY_PARAM_NAME_RE.match(value):
            raise ValueError(
                f"cache_key_param {value!r} is not a valid request-body param name"
            )
        return value

    @field_validator("extra_headers")
    @classmethod
    def _validate_extra_headers(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if not _HEADER_NAME_RE.match(name):
                raise ValueError(
                    f"extra_headers key {name!r} is not a valid HTTP header name"
                )
        return value
