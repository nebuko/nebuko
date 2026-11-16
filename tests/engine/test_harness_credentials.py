"""ADR 0017 rule 1 — Claude Code credentials are binary-only."""

from __future__ import annotations

import pytest

from nebuko.engine.harness.credentials import (
    ClaudeCredentialError,
    backend_credentials_error,
    enforce_backend_credentials,
    is_claude_code_model,
    is_subscription_token,
    scrub_subscription_tokens,
    uses_claude_code_credentials,
)


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
