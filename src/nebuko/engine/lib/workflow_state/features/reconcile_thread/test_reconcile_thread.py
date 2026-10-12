"""Tests for the thread-ledger reconciler (issue #872)."""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
)

from workflow_state.features.reconcile_thread import (  # noqa: E402
    DEFAULT_TRUST_ALLOWLIST,
    reconcile_thread,
)
from workflow_state.sub_domains.thread_record import (  # noqa: E402
    ThreadRecord,
    ThreadRecordKind,
)

TRIGGER_ID = 1
TRIGGER_LOGIN = "olivier-norture"
ISSUE_ID = 5794187059


def _user(user_id=TRIGGER_ID, login=TRIGGER_LOGIN, kind="User") -> dict:
    return {"id": user_id, "login": login, "type": kind}


def _issue(
    *, issue_id=ISSUE_ID, user=None, association="OWNER", created="2026-10-01T00:00:00Z"
) -> dict:
    return {
        "id": issue_id,
        "html_url": "https://github.com/nebuko/nebuko/issues/872",
        "user": user or _user(),
        "author_association": association,
        "created_at": created,
    }


def _comment(
    comment_id,
    body,
    *,
    user=None,
    association="MEMBER",
    created="2026-10-02T00:00:00Z",
) -> dict:
    return {
        "id": comment_id,
        "html_url": (
            "https://github.com/nebuko/nebuko/issues/872#issuecomment-"
            f"{comment_id}"
        ),
        "body": body,
        "user": user or _user(),
        "author_association": association,
        "created_at": created,
    }


def _reconcile(records, issue, comments, **kwargs):
    params = {"trigger_user_id": TRIGGER_ID, "trigger_login": TRIGGER_LOGIN}
    params.update(kwargs)
    return reconcile_thread(records, issue=issue, comments=comments, **params)


class TestOrderingAndClassification:
    def test_issue_body_is_folded_first(self):
        ledger = _reconcile([], _issue(), [_comment(11, "hello")])
        assert [record.id for record in ledger] == [ISSUE_ID, 11]
        assert ledger[0].kind is ThreadRecordKind.ISSUE_BODY

    def test_comments_keep_api_order(self):
        ledger = _reconcile(
            [], _issue(), [_comment(11, "one"), _comment(12, "two")]
        )
        assert [record.id for record in ledger] == [ISSUE_ID, 11, 12]

    def test_comments_are_classified(self):
        # Realistic PAT payloads: every comment is posted by the PAT owner as
        # ``type: "User"``; bot-ness must come from the body marker alone.
        ledger = _reconcile(
            [],
            _issue(),
            [
                _comment(11, "/nebuko approve gate"),
                _comment(
                    12,
                    "<!-- bot-workflow-terminal -->\n<!-- bot-agent-dev -->\n"
                    "*Dev answer*",
                ),
                _comment(13, "just a human thought"),
                _comment(14, "<!-- bot-workflow-terminal -->\nengine note"),
            ],
        )
        by_id = {record.id: record.kind for record in ledger}
        assert by_id[11] is ThreadRecordKind.COMMAND
        assert by_id[12] is ThreadRecordKind.AGENT_OUTPUT
        assert by_id[13] is ThreadRecordKind.HUMAN_FEEDBACK
        assert by_id[14] is ThreadRecordKind.ENGINE_OUTPUT

    def test_legacy_agent_marker_without_universal_marker_is_agent_output(self):
        # Pre-universal-marker issues: the legacy line-anchored agent token in
        # the opening window is still enough, with a ``type: "User"`` author.
        ledger = _reconcile(
            [], _issue(), [_comment(11, "<!-- bot-agent-dev -->\n*answer*")]
        )
        assert {record.id: record.kind for record in ledger}[11] is (
            ThreadRecordKind.AGENT_OUTPUT
        )

    def test_engine_notices_are_engine_output_for_user_authored_comments(self):
        comments = [
            _comment(
                11,
                "<!-- bot-workflow-terminal -->\n**dev agent started** "
                "[View workflow run logs](https://x)",
            ),
            _comment(12, "<!-- bot-workflow-terminal -->\n<!-- bot-human-action -->\n"),
            _comment(
                13,
                "<!-- bot-workflow-terminal -->\n"
                "The agent crashed or timed out before producing output",
            ),
            _comment(
                14,
                "<!-- bot-workflow-terminal -->\n"
                "The agent did not produce a final message",
            ),
        ]
        ledger = _reconcile([], _issue(), comments)
        by_id = {record.id: record.kind for record in ledger}
        assert {by_id[11], by_id[12], by_id[13], by_id[14]} == {
            ThreadRecordKind.ENGINE_OUTPUT
        }

    def test_a_non_bot_quoting_a_marker_is_not_agent_output(self):
        # GitHub quote-reply copies the marker into the quoted text; a human
        # author must not be classified as agent output (issue #872 review).
        ledger = _reconcile(
            [], _issue(), [_comment(11, "> <!-- bot-agent-dev -->\n> the answer")]
        )
        assert {record.id: record.kind for record in ledger}[11] is (
            ThreadRecordKind.HUMAN_FEEDBACK
        )

    def test_a_human_quoting_the_universal_marker_stays_human_feedback(self):
        # A quote-only reply carries the universal marker behind ``>``; with no
        # top-level marker it must not impersonate a bot author.
        ledger = _reconcile(
            [],
            _issue(),
            [_comment(11, "> <!-- bot-workflow-terminal -->\n> quoted answer")],
        )
        assert {record.id: record.kind for record in ledger}[11] is (
            ThreadRecordKind.HUMAN_FEEDBACK
        )

    def test_a_github_app_bot_without_a_marker_is_still_bot(self):
        # ``user.type`` remains an OR-signal for a genuine GitHub App bot.
        ledger = _reconcile(
            [],
            _issue(),
            [_comment(11, "plain body", user=_user(99, "nebuko[bot]", "Bot"))],
        )
        assert {record.id: record.kind for record in ledger}[11] is (
            ThreadRecordKind.ENGINE_OUTPUT
        )

    def test_issue_none_yields_comment_only_ledger(self):
        ledger = _reconcile([], None, [_comment(11, "hello")])
        assert [record.id for record in ledger] == [11]


class TestDedupAndDeletion:
    def test_duplicate_comment_ids_are_recorded_once(self):
        ledger = _reconcile(
            [], _issue(), [_comment(11, "one"), _comment(11, "one again")]
        )
        assert [record.id for record in ledger].count(11) == 1

    def test_vanished_ids_are_kept_and_flagged_deleted(self):
        previous = [
            ThreadRecord(
                kind=ThreadRecordKind.AGENT_OUTPUT,
                id=999,
                user_id=5,
                user_login="dev",
            )
        ]
        ledger = _reconcile(previous, _issue(), [_comment(11, "hello")])
        by_id = {record.id: record for record in ledger}
        assert by_id[999].deleted is True
        assert by_id[999].kind is ThreadRecordKind.AGENT_OUTPUT

    def test_present_ids_are_not_flagged_deleted(self):
        ledger = _reconcile([], _issue(), [_comment(11, "hello")])
        assert all(not record.deleted for record in ledger)

    def test_already_deleted_record_stays_deleted(self):
        previous = [
            ThreadRecord(
                kind=ThreadRecordKind.COMMAND, id=999, user_id=5, user_login="dev"
            ).model_copy(update={"deleted": True})
        ]
        ledger = _reconcile(previous, _issue(), [_comment(11, "hello")])
        assert {record.id: record.deleted for record in ledger}[999] is True

    def test_existing_record_is_recomputed_from_the_payload(self):
        previous = [
            ThreadRecord(
                kind=ThreadRecordKind.HUMAN_FEEDBACK,
                id=11,
                user_id=TRIGGER_ID,
                user_login=TRIGGER_LOGIN,
                trusted=False,
            )
        ]
        observed = _comment(11, "feedback", association="MEMBER")
        ledger = _reconcile(previous, _issue(), [observed])
        assert {record.id: record for record in ledger}[11].trusted is True


class TestTrustResolution:
    def test_allowlisted_trigger_author_is_trusted(self):
        ledger = _reconcile(
            [], None, [_comment(11, "feedback", association="MEMBER")]
        )
        assert ledger[0].trusted is True

    def test_non_allowlisted_association_is_not_trusted(self):
        ledger = _reconcile(
            [], None, [_comment(11, "feedback", association="CONTRIBUTOR")]
        )
        assert ledger[0].trusted is False

    def test_unknown_trigger_author_is_not_trusted(self):
        ledger = _reconcile(
            [],
            None,
            [_comment(11, "feedback", association="MEMBER")],
            trigger_user_id=0,
            trigger_login="",
        )
        assert ledger[0].trusted is False

    def test_login_fallback_matches_when_id_unknown(self):
        ledger = _reconcile(
            [],
            None,
            [_comment(11, "feedback", association="MEMBER")],
            trigger_user_id=0,
            trigger_login=TRIGGER_LOGIN,
        )
        assert ledger[0].trusted is True

    def test_other_user_is_not_trusted_even_when_allowlisted(self):
        ledger = _reconcile(
            [],
            None,
            [
                _comment(
                    11,
                    "feedback",
                    association="MEMBER",
                    user=_user(42, "someone-else"),
                )
            ],
        )
        assert ledger[0].trusted is False

    def test_login_fallback_rejects_a_different_login(self):
        ledger = _reconcile(
            [],
            None,
            [_comment(11, "feedback", user=_user(42, "someone-else"))],
            trigger_user_id=0,
            trigger_login=TRIGGER_LOGIN,
        )
        assert ledger[0].trusted is False

    def test_issue_body_uses_the_same_trust_rule(self):
        trusted = _reconcile([], _issue(association="OWNER"), [])
        untrusted = _reconcile(
            [], _issue(association="NONE", user=_user(42, "stranger")), []
        )
        assert trusted[0].trusted is True
        assert untrusted[0].trusted is False

    def test_allowlist_default_matches_the_caller_auth_gate(self):
        assert DEFAULT_TRUST_ALLOWLIST == ("OWNER", "MEMBER", "COLLABORATOR")
