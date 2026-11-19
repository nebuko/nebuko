"""Anthropic subscription credentials are bound to the Claude Code binary.

ADR 0017 rule 1: a ``CLAUDE_CODE_OAUTH_TOKEN`` (or a ``claude-code/*`` model
path) may only be exercised through the *unmodified* Claude Code binary. Running
the same subscription token through the OpenHands/litellm loop would violate
Anthropic's terms, so the offline linter (rule ``C12``) and the run path both
reject the combination — and the converse, a ``claude-code`` backend fed by a
non-Claude provider.

The rule is pure (no config or SDK imports) so both consumers share exactly one
implementation.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass

__all__ = [
    "CLAUDE_CODE_BACKEND",
    "CLAUDE_CODE_MODEL_PREFIX",
    "DEFAULT_BACKEND",
    "SUBSCRIPTION_TOKEN_ENVS",
    "CONTINUATION_CALLER_ASSOCIATIONS",
    "CONTINUATION_MARKERS",
    "ClaudeCredentialError",
    "ContinuationContext",
    "ContinuationDecision",
    "authorize_bot_continuation",
    "backend_credentials_error",
    "enforce_backend_credentials",
    "is_bot_actor",
    "is_claude_code_model",
    "is_subscription_token",
    "scrub_subscription_tokens",
    "uses_claude_code_credentials",
]

CLAUDE_CODE_BACKEND = "claude-code"
DEFAULT_BACKEND = "openhands"

#: Provider ``api_token_env`` names that carry an Anthropic *subscription*
#: credential rather than a metered API key.
SUBSCRIPTION_TOKEN_ENVS: frozenset[str] = frozenset({"CLAUDE_CODE_OAUTH_TOKEN"})

#: ``model_path`` prefix that routes a model through the Claude Code binary.
CLAUDE_CODE_MODEL_PREFIX = "claude-code/"


class ClaudeCredentialError(RuntimeError):
    """A config pairs Claude Code credentials with another backend (ADR 0017).

    Deliberately *not* an ``EngineAgentUnavailable``: that error makes the engine
    fall back to the legacy subprocess seam, which would run the same illegal
    pairing a second time. This one is terminal.
    """


def is_subscription_token(api_token_env: str | None) -> bool:
    """True when ``api_token_env`` names an Anthropic subscription token."""
    return bool(api_token_env) and api_token_env in SUBSCRIPTION_TOKEN_ENVS


def scrub_subscription_tokens(env: MutableMapping[str, str]) -> tuple[str, ...]:
    """Remove every subscription token from a process environment.

    ADR 0017 A3: the token is exported into the job environment of *every*
    comment-triggered run, and a backend whose tools spawn subprocesses (the
    OpenHands ``terminal``) hands the engine's own ``os.environ`` to them. A
    prompt-injected agent on such a backend could therefore read and exfiltrate
    the token — masking covers logs, not exfiltration. Only the Claude Code
    binary needs the credential, so every other backend drops it first.

    Returns:
        The names that were present and removed (for one log line).
    """
    removed: list[str] = []
    for name in sorted(SUBSCRIPTION_TOKEN_ENVS):
        if env.pop(name, None):
            removed.append(name)
    return tuple(removed)


def is_claude_code_model(model_path: str | None) -> bool:
    """True when ``model_path`` selects a Claude Code model alias."""
    return bool(model_path) and model_path.startswith(CLAUDE_CODE_MODEL_PREFIX)


def uses_claude_code_credentials(
    model_path: str | None = None, api_token_env: str | None = None
) -> bool:
    """True when the llm/provider pair is Claude Code-only."""
    return is_subscription_token(api_token_env) or is_claude_code_model(model_path)


def backend_credentials_error(
    backend: str | None,
    model_path: str | None = None,
    api_token_env: str | None = None,
) -> str | None:
    """The ADR 0017 violation for one (backend, model, provider) triple.

    Returns ``None`` when the pairing is legal, otherwise a one-sentence
    explanation naming both sides of the mismatch.
    """
    name = backend or DEFAULT_BACKEND
    claude_credentials = uses_claude_code_credentials(model_path, api_token_env)
    if name == CLAUDE_CODE_BACKEND and not claude_credentials:
        # A metered Anthropic key is a legal Claude Code credential (ADR 0017
        # Lane K), so the fix is always the model path, never the key name.
        return (
            f"backend '{CLAUDE_CODE_BACKEND}' needs a Claude Code model "
            f"(model_path '{CLAUDE_CODE_MODEL_PREFIX}*'); a subscription token "
            f"(api_token_env '{sorted(SUBSCRIPTION_TOKEN_ENVS)[0]}') also "
            f"implies it, and a metered key such as 'ANTHROPIC_API_KEY' is fine "
            f"as long as the model path is '{CLAUDE_CODE_MODEL_PREFIX}*'; got "
            f"model_path '{model_path or ''}' with api_token_env "
            f"'{api_token_env or ''}'"
        )
    if name != CLAUDE_CODE_BACKEND and claude_credentials:
        return (
            f"backend '{name}' must not use Claude Code credentials (model_path "
            f"'{model_path or ''}', api_token_env '{api_token_env or ''}'): an "
            f"Anthropic subscription token may only run through the "
            f"'{CLAUDE_CODE_BACKEND}' backend (ADR 0017)"
        )
    return None


def enforce_backend_credentials(
    backend: str | None,
    model_path: str | None = None,
    api_token_env: str | None = None,
) -> None:
    """Raise :class:`ClaudeCredentialError` on an ADR 0017 violation.

    The run-time twin of the ``C12`` lint rule: a config that bypasses the
    linter still fails here, before the provider is called.
    """
    message = backend_credentials_error(backend, model_path, api_token_env)
    if message is not None:
        raise ClaudeCredentialError(message)


#: Markers a bot-authored continuation comment must carry in its first lines.
#: Matched on the header only: a quoted marker deeper in the body does not
#: count (issue #1361).
CONTINUATION_MARKERS: tuple[str, ...] = ("<!-- workflow-continue -->", "<!-- bot-trigger:")

#: Lines of the trigger comment that are inspected for a continuation marker.
CONTINUATION_HEADER_LINES = 2

#: Associations that may keep a bot continuation alive (the CALLER_AUTH rule).
CONTINUATION_CALLER_ASSOCIATIONS: frozenset[str] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

_API_KEY_LANE_HINT = (
    " Use the API-key lane instead: a provider API key secret with a "
    "non-claude-code backend in the agent config."
)


def is_bot_actor(login: str | None, actor_type: str | None = None) -> bool:
    """True for a GitHub ``Bot`` account or a ``*[bot]`` login."""
    name = (login or "").strip()
    return (actor_type or "").strip() == "Bot" or name.endswith("[bot]")


@dataclass(frozen=True)
class ContinuationContext:
    """Facts behind a bot-triggered claude-code run (ADR 0017, #1424).

    Attributes:
        trigger_login: Login of the comment author that triggered the run.
        trigger_type: GitHub account type of that author (``Bot`` / ``User``).
        trusted_bots: Bot logins allowed to continue a human-started workflow.
        comment_header: The trigger comment's first lines; only these are
            searched for a continuation marker.
        started_by: ``started_by`` of the previous WORKFLOW_STATE, if any.
        credential_owner: Login bound to the credential in the per-user lane;
            ``None`` in the shared lane, where no owner binding applies.
    """

    trigger_login: str
    trigger_type: str
    trusted_bots: tuple[str, ...]
    comment_header: str
    started_by: str | None
    credential_owner: str | None = None


@dataclass(frozen=True)
class ContinuationDecision:
    """Outcome of :func:`authorize_bot_continuation`.

    ``failed_rule`` names the first rule that refused the run; empty when
    allowed. ``api_key_lane_applies`` is set only where the API-key lane is the
    right answer (untrusted author, or no human-started workflow behind it).
    """

    allowed: bool
    failed_rule: str = ""
    api_key_lane_applies: bool = False
    detail: str = ""

    def refusal_message(self) -> str:
        """The terminal message for a refused run; names the failed rule."""
        hint = _API_KEY_LANE_HINT if self.api_key_lane_applies else ""
        return (
            f"claude-code run refused for bot trigger: rule '{self.failed_rule}' "
            f"failed ({self.detail}).{hint}"
        )


def authorize_bot_continuation(
    ctx: ContinuationContext,
    association_of: Callable[[str], str | None],
) -> ContinuationDecision:
    """Decide whether a bot-triggered claude-code run may proceed.

    Allowed only when the author is a trusted bot, the header carries a
    continuation marker, the previous state was started by a non-bot human who
    still holds a CALLER_AUTH association, and (per-user lane) that human is the
    credential owner. ``association_of`` maps a login to its association or
    ``None`` when the lookup fails; a failed lookup refuses.
    """
    if not is_bot_actor(ctx.trigger_login, ctx.trigger_type) or ctx.trigger_login not in ctx.trusted_bots:
        return ContinuationDecision(
            False, "trusted-bot", True, f"author '{ctx.trigger_login}' is not a trusted bot"
        )

    header = "\n".join(ctx.comment_header.splitlines()[:CONTINUATION_HEADER_LINES])
    if not any(marker in header for marker in CONTINUATION_MARKERS):
        return ContinuationDecision(
            False, "continuation-marker", False, "no continuation marker in the comment header"
        )

    started_by = (ctx.started_by or "").strip()
    if not started_by or is_bot_actor(started_by):
        return ContinuationDecision(
            False, "human-started-state", True, "no human-started workflow state behind the comment"
        )

    association = association_of(started_by)
    if association is None or association not in CONTINUATION_CALLER_ASSOCIATIONS:
        return ContinuationDecision(
            False,
            "caller-association",
            False,
            f"'{started_by}' is no longer OWNER/MEMBER/COLLABORATOR (association {association or 'unknown'})",
        )

    if ctx.credential_owner is not None and ctx.credential_owner != started_by:
        return ContinuationDecision(
            False,
            "credential-owner",
            False,
            f"started_by '{started_by}' differs from the credential owner '{ctx.credential_owner}'",
        )

    return ContinuationDecision(True)
