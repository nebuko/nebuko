"""ADR 0017 rule 1 — Claude Code credentials are binary-only."""

from __future__ import annotations

import pytest

from nebuko.engine.harness.credentials import (
    ClaudeCredentialError,
    ContinuationContext,
    authorize_bot_continuation,
    backend_credentials_error,
    enforce_backend_credentials,
    is_bot_actor,
    is_claude_code_model,
    is_subscription_token,
    scrub_subscription_tokens,
    uses_claude_code_credentials,
)

BOT = "nebuko[bot]"
HUMAN_ASSOC = {"alice": "OWNER"}


def _ctx(**overrides):
    fields = {
        "trigger_login": BOT,
        "trigger_type": "Bot",
        "trusted_bots": (BOT,),
        "comment_header": "<!-- bot-workflow-terminal --><!-- workflow-continue -->\nnext",
        "started_by": "alice",
        "credential_owner": None,
    }
    fields.update(overrides)
    return ContinuationContext(**fields)


def _assoc(login):
    return HUMAN_ASSOC.get(login)


def test_bot_continuation_with_human_started_state_is_allowed():
    decision = authorize_bot_continuation(_ctx(), _assoc)
    assert decision.allowed, decision


def test_is_bot_actor_recognizes_type_and_suffix():
    assert is_bot_actor("octocat", "Bot")
    assert is_bot_actor("renovate[bot]", "")
    assert not is_bot_actor("octocat", "User")


@pytest.mark.parametrize(
    "overrides",
    [
        {"trigger_login": "evil[bot]", "comment_header": "<!-- workflow-continue -->"},
        {"comment_header": "quoted:\n\n> <!-- workflow-continue -->"},
        {"comment_header": "plain comment"},
        {"started_by": None},
        {"started_by": BOT},
        {"started_by": "mallory"},
        {"credential_owner": "bob"},
    ],
    ids=[
        "untrusted-bot",
        "marker-quoted-deep",
        "no-marker",
        "no-state",
        "bot-started",
        "association-lost",
        "owner-mismatch",
    ],
)
def test_bot_continuation_refusals_name_the_failed_rule(overrides):
    decision = authorize_bot_continuation(_ctx(**overrides), _assoc)
    assert not decision.allowed
    assert decision.failed_rule
    assert decision.failed_rule in decision.refusal_message()


def test_failed_association_lookup_refuses():
    decision = authorize_bot_continuation(_ctx(), lambda _login: None)
    assert not decision.allowed
    assert decision.failed_rule == "caller-association"


def test_api_key_lane_hint_only_where_it_applies():
    untrusted = authorize_bot_continuation(_ctx(trigger_login="evil[bot]", trusted_bots=()), _assoc)
    assert "API-key lane" in untrusted.refusal_message()
    lost = authorize_bot_continuation(_ctx(started_by="mallory"), _assoc)
    assert "API-key lane" not in lost.refusal_message()


def test_subscription_token_is_recognized():
    assert is_subscription_token("CLAUDE_CODE_OAUTH_TOKEN")
    assert not is_subscription_token("ANTHROPIC_API_KEY")
    assert not is_subscription_token(None)


def test_claude_code_model_path_is_recognized():
    assert is_claude_code_model("claude-code/sonnet")
    assert not is_claude_code_model("anthropic/claude-sonnet")
    assert not is_claude_code_model(None)


def test_either_side_marks_the_pair_as_claude_code_only():
    assert uses_claude_code_credentials("claude-code/opus", None)
    assert uses_claude_code_credentials(None, "CLAUDE_CODE_OAUTH_TOKEN")
    assert not uses_claude_code_credentials("openai/glm-5.3", "OPENAI_API_KEY")


@pytest.mark.parametrize(
    ("backend", "model_path", "token"),
    [
        (None, "anthropic/claude-sonnet", "CLAUDE_CODE_OAUTH_TOKEN"),
        ("openhands", "claude-code/sonnet", "ANTHROPIC_API_KEY"),
        ("claude-code", "anthropic/claude-sonnet", "ANTHROPIC_API_KEY"),
    ],
)
def test_illegal_pairings_are_reported(backend, model_path, token):
    assert backend_credentials_error(backend, model_path, token) is not None


@pytest.mark.parametrize(
    ("backend", "model_path", "token"),
    [
        ("claude-code", "claude-code/sonnet", "CLAUDE_CODE_OAUTH_TOKEN"),
        # A metered Anthropic key on the claude-code backend is still Claude Code.
        ("claude-code", "claude-code/sonnet", "ANTHROPIC_API_KEY"),
        (None, "openai/glm-5.3", "OPENAI_API_KEY"),
        ("openhands", "anthropic/claude-sonnet", "ANTHROPIC_API_KEY"),
    ],
)
def test_legal_pairings_pass(backend, model_path, token):
    assert backend_credentials_error(backend, model_path, token) is None


def test_enforce_raises_a_terminal_error():
    with pytest.raises(ClaudeCredentialError):
        enforce_backend_credentials(
            None, "anthropic/claude-sonnet", "CLAUDE_CODE_OAUTH_TOKEN"
        )


def test_converse_message_points_at_the_model_path_not_the_key():
    """A metered key is a legal Claude Code credential (ADR 0017 Lane K)."""
    message = backend_credentials_error(
        "claude-code", "anthropic/claude-sonnet", "ANTHROPIC_API_KEY"
    )
    assert message is not None
    assert "claude-code/*" in message
    assert "ANTHROPIC_API_KEY" in message


def test_scrub_removes_the_subscription_token_and_reports_it():
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "PATH": "/bin"}
    assert scrub_subscription_tokens(env) == ("CLAUDE_CODE_OAUTH_TOKEN",)
    assert env == {"PATH": "/bin"}


def test_scrub_leaves_metered_keys_and_an_absent_token_alone():
    env = {"ANTHROPIC_API_KEY": "key"}
    assert scrub_subscription_tokens(env) == ()
    assert env == {"ANTHROPIC_API_KEY": "key"}
