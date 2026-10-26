"""Runtime view of LLM provider configuration — per-call HTTP/body decoration.

Static provider shape lives in repo config; the only per-issue value (the
session id a gateway such as OpenCode Go expects, or the prompt-cache key
Mistral expects in the request body) is stamped by the engine and flows in
through the environment. This module is the seam where the two meet: the
request path consumes only the :class:`LLMProviderRuntime` port, so config can
never carry a per-issue value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

if TYPE_CHECKING:
    from nebuko.engine.lib.config import ProviderConfig

__all__ = [
    "LLMProviderRuntime",
    "StaticProviderRuntime",
    "IssueSessionRuntime",
    "IssueCacheKeyRuntime",
    "SESSION_ID_ENV",
    "build_provider_runtime",
]

# The engine stamps this once per run from the trigger's issue number; see
# ``run_loop._agent_env``. Absent (legacy/subprocess path) → no per-issue decoration.
SESSION_ID_ENV = "LLM_SESSION_ID"


class LLMProviderRuntime(Protocol):
    """The runtime view of LLM provider configuration.

    A runtime contributes the HTTP headers and request-body parameters a
    provider request should carry. Static client identity and the
    engine-supplied per-issue overrides are separate implementations, so a
    per-issue value cannot leak into config.
    """

    def extra_headers(self) -> Mapping[str, str]:
        ...

    def extra_body(self) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class StaticProviderRuntime:
    """Adapter over ``ProviderConfig`` — forward the static identity verbatim."""

    provider: ProviderConfig

    def extra_headers(self) -> Mapping[str, str]:
        headers: dict[str, str] = {}
        if self.provider.user_agent:
            headers["User-Agent"] = self.provider.user_agent
        headers.update(self.provider.extra_headers)
        return headers

    def extra_body(self) -> Mapping[str, Any]:
        return {}


@dataclass(frozen=True)
class IssueSessionRuntime:
    """Adapter over the engine-stamped issue identity — stamp the session header."""

    session_header: str
    session_id: str

    def extra_headers(self) -> Mapping[str, str]:
        return {self.session_header: self.session_id}

    def extra_body(self) -> Mapping[str, Any]:
        return {}


@dataclass(frozen=True)
class IssueCacheKeyRuntime:
    """Adapter over the engine-stamped issue identity — stamp the cache-key body param."""

    cache_key_param: str
    session_id: str

    def extra_headers(self) -> Mapping[str, str]:
        return {}

    def extra_body(self) -> Mapping[str, Any]:
        return {self.cache_key_param: self.session_id}


def build_provider_runtime(
    provider: ProviderConfig, env: Mapping[str, str]
) -> Sequence[LLMProviderRuntime]:
    """Assemble the decorators for one provider, in factory order.

    The static runtime is always present. A per-issue runtime joins it only when
    the provider declares a name (header or body param) **and** the engine
    stamped an id — vendor-neutral: with no declared name nothing is sent, even
    when the env var exists. On a collision the later (per-issue) runtime wins.
    """
    runtimes: list[LLMProviderRuntime] = [StaticProviderRuntime(provider)]
    session_id = env.get(SESSION_ID_ENV)
    if provider.session_header and session_id:
        runtimes.append(IssueSessionRuntime(provider.session_header, session_id))
    if provider.cache_key_param and session_id:
        runtimes.append(IssueCacheKeyRuntime(provider.cache_key_param, session_id))
    return runtimes
