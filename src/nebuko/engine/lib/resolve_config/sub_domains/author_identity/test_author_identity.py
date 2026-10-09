"""Tests for the AuthorIdentity gate-authority value object (issue #648)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.sub_domains.author_identity.author_identity import (  # noqa: E402
    BOT_LOGIN_DENYLIST,
    AuthorIdentity,
    build_author_identity,
)

GATE_CMD = "/nebuko approve gate"


class TestAuthorIdentityModel:
    def test_defaults(self):
        author = AuthorIdentity(login="alice")
        assert author.is_bot is False
        assert author.association == ""
        assert author.is_machine_comment is False

    def test_frozen(self):
        author = AuthorIdentity(login="alice")
        with pytest.raises(Exception):
            author.login = "bob"  # type: ignore[misc]

    def test_privileged_human_may_decide(self):
        author = AuthorIdentity(login="alice", association="MEMBER")
        assert author.may_decide_gate() is True

    def test_owner_and_collaborator_allowed(self):
        assert AuthorIdentity(login="a", association="OWNER").may_decide_gate()
        assert AuthorIdentity(
            login="a", association="COLLABORATOR"
        ).may_decide_gate()

    def test_bot_may_not_decide(self):
        author = AuthorIdentity(login="github-actions[bot]", is_bot=True, association="MEMBER")
        assert author.may_decide_gate() is False

    def test_machine_comment_may_not_decide(self):
        author = AuthorIdentity(
            login="alice", association="MEMBER", is_machine_comment=True
        )
        assert author.may_decide_gate() is False

    @pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", ""])
    def test_unprivileged_association_may_not_decide(self, association):
        author = AuthorIdentity(login="alice", association=association)
        assert author.may_decide_gate() is False

    def test_custom_allowlist(self):
        author = AuthorIdentity(login="alice", association="CONTRIBUTOR")
        assert author.may_decide_gate(allowlist=("CONTRIBUTOR",)) is True


class TestBuildAuthorIdentity:
    def test_bot_authored_gate_command_is_inert(self):
        """A bot-account comment asking to approve is never authoritative."""
        body = (
            "<!-- bot-workflow-terminal -->\n"
            f"{GATE_CMD}\n"
            "<!-- bot-agent-dev -->"
        )
        author = build_author_identity(
            login="github-actions[bot]",
            user_type="Bot",
            association="MEMBER",
            trigger_body=body,
        )
        assert author.is_bot is True
        assert author.is_machine_comment is True
        assert author.may_decide_gate() is False

    def test_privileged_human_plain_command(self):
        author = build_author_identity(
            login="olivier-norture",
            user_type="User",
            association="MEMBER",
            trigger_body=GATE_CMD,
        )
        assert author.is_bot is False
        assert author.is_machine_comment is False
        assert author.may_decide_gate() is True

    def test_human_echoing_machine_token_is_inert(self):
        author = build_author_identity(
            login="alice",
            user_type="User",
            association="MEMBER",
            trigger_body=f"{GATE_CMD}\n<!-- bot-trigger: dev -->",
        )
        assert author.is_machine_comment is True
        assert author.may_decide_gate() is False

    def test_bot_type_flag(self):
        author = build_author_identity(
            login="some-app", user_type="Bot", association="MEMBER", trigger_body=GATE_CMD
        )
        assert author.is_bot is True

    def test_denylisted_machine_user_with_user_type(self):
        """Machine users can be typed User (GH discussion #65546)."""
        denylisted = BOT_LOGIN_DENYLIST[0]
        author = build_author_identity(
            login=denylisted,
            user_type="User",
            association="MEMBER",
            trigger_body=GATE_CMD,
        )
        assert author.is_bot is True
        assert author.may_decide_gate() is False

    def test_bot_suffix_login(self):
        author = build_author_identity(
            login="custom-bot[bot]",
            user_type="User",
            association="OWNER",
            trigger_body=GATE_CMD,
        )
        assert author.is_bot is True

    def test_field_whitespace_is_stripped(self):
        author = build_author_identity(
            login=" alice ", user_type=" User ", association=" MEMBER ", trigger_body=""
        )
        assert author.login == "alice"
        assert author.association == "MEMBER"
        assert author.is_bot is False

    def test_none_safe(self):
        author = build_author_identity(
            login="", user_type="", association="", trigger_body=""
        )
        assert author.is_bot is False
        assert author.may_decide_gate() is False
