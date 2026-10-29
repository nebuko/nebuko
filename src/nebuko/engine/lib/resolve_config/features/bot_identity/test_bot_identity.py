"""Red tests for the BotIdentity contract (issue #1218)."""

import pytest

from bot_identity import BotIdentity, default_identity, from_config


def test_default_identity_is_nebuko_app_bot():
    identity = default_identity()
    assert identity.login == "nebuko[bot]"
    assert identity.display_name == "nebuko"


def test_mention_token_prefixes_login_with_at():
    identity = BotIdentity(login="nebuko[bot]", display_name="nebuko", email="")
    assert identity.mention_token() == "@nebuko[bot]"


def test_commit_author_returns_name_email_pair():
    identity = BotIdentity(login="nebuko[bot]", display_name="nebuko", email="")
    name, email = identity.commit_author()
    assert name == "nebuko"
    assert "@" in email


def test_is_app_bot_suffix_rule():
    app_bot = BotIdentity(login="nebuko[bot]", display_name="nebuko", email="")
    machine_user = BotIdentity(login="nebuko-agent", display_name="nebuko", email="")
    assert app_bot.is_app_bot() is True
    assert machine_user.is_app_bot() is False


def test_from_config_overrides_defaults():
    config = {"bot": {"login": "nebuko-agent", "display_name": "Nebuko Agent", "email": "agent@nebuko.dev"}}
    identity = from_config(config)
    assert identity.login == "nebuko-agent"
    assert identity.display_name == "Nebuko Agent"
    assert identity.email == "agent@nebuko.dev"


def test_from_config_missing_block_falls_back_to_defaults():
    identity = from_config({})
    assert identity.login == "nebuko[bot]"
    assert identity.display_name == "nebuko"


def test_from_config_partial_override_keeps_default_email():
    config = {"bot": {"login": "nebuko-agent", "display_name": "Nebuko Agent"}}
    identity = from_config(config)
    assert identity.login == "nebuko-agent"
    assert identity.display_name == "Nebuko Agent"
    assert identity.email == ""
