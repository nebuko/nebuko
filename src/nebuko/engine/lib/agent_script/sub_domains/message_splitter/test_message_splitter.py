"""Tests for multi-comment answer splitting (issue #913)."""

from nebuko.engine.lib.agent_script.sub_domains.message_splitter.message_splitter import (
    CommentPart,
    split_comment_body,
)
from nebuko.engine.lib.config import CommentSizePolicy
from nebuko.engine.lib.prepare_comment import ANSWER_CONTINUATION_TOKEN


def _lines(text: str, count: int) -> str:
    """``count`` paragraph-separated lines, each safely under a small budget."""
    return "\n\n".join(f"{text} {i}" for i in range(count))


class TestSinglePart:
    def test_short_body_posts_as_one_headerless_part(self):
        parts = split_comment_body("hello world", CommentSizePolicy())

        assert parts == [CommentPart(index=1, total=1, body="hello world")]

    def test_head_is_prepended_without_a_header(self):
        parts = split_comment_body("body", CommentSizePolicy(), head="HEAD\n\n")

        assert len(parts) == 1
        assert parts[0].body == "HEAD\n\nbody"
        assert "*(Part" not in parts[0].body


class TestMultiPart:
    def _policy(self) -> CommentSizePolicy:
        return CommentSizePolicy(max_comment_chars=200, max_parts=10)

    def test_each_part_within_budget_and_ordered(self):
        body = _lines("paragraph", 40)
        parts = split_comment_body(body, self._policy())

        assert len(parts) > 1
        assert [p.index for p in parts] == list(range(1, len(parts) + 1))
        assert all(p.total == len(parts) for p in parts)
        assert all(len(p.body) <= 200 for p in parts)

    def test_headers_are_emitted_on_every_part(self):
        body = _lines("paragraph", 40)
        parts = split_comment_body(body, self._policy())

        for part in parts:
            header = f"*(Part {part.index}/{part.total})*\n\n"
            # Continuation parts carry the inertness stamp ahead of their
            # header (issue #1002); part 1 carries neither stamp nor marker.
            if part.index == 1:
                assert part.body.startswith(header)
            else:
                assert part.body.startswith(ANSWER_CONTINUATION_TOKEN + "\n" + header)

    def test_continuation_parts_are_stamped_inert(self):
        """Only part 1 may re-trigger the engine (issue #1002)."""
        parts = split_comment_body(_lines("paragraph", 40), self._policy())

        assert ANSWER_CONTINUATION_TOKEN not in parts[0].body
        for part in parts[1:]:
            assert ANSWER_CONTINUATION_TOKEN in part.body

    def test_content_reassembles_without_loss(self):
        body = _lines("paragraph", 40)
        parts = split_comment_body(body, self._policy())

        reassembled = "".join(
            part.body[len(f"*(Part {part.index}/{part.total})*\n\n") :]
            for part in parts
        )
        # Splitting strips the exact delimiters it split on, so compare tokens.
        assert reassembled.count("paragraph") == body.count("paragraph")

    def test_splits_at_paragraph_boundary(self):
        # The budget leaves room for an 80-char paragraph *plus* the
        # continuation part's stamp and bot marker (issue #1002).
        body = "\n\n".join(["A" * 80, "B" * 80, "C" * 80])
        parts = split_comment_body(body, CommentSizePolicy(max_comment_chars=150, max_parts=10))

        assert parts[0].body.endswith("A" * 80)
        assert "B" * 80 in parts[1].body

    def test_continuation_payload_is_neutralized(self):
        """A quoted trigger payload must not survive on a continuation part (#1002)."""
        body = "\n\n".join(
            ["A" * 80, "B" * 80, "C" * 80]
            + ["<!-- workflow-continue -->\n<!-- bot-trigger: architect -->\n@@dev"]
        )
        parts = split_comment_body(body, CommentSizePolicy(max_comment_chars=150, max_parts=10))

        for part in parts[1:]:
            assert "<!-- workflow-continue -->" not in part.body
            assert "<!-- bot-trigger:" not in part.body
            assert "@@dev" not in part.body


class TestHeadPlacement:
    def test_head_lands_only_on_the_first_part(self):
        policy = CommentSizePolicy(max_comment_chars=200, max_parts=10)
        parts = split_comment_body(_lines("paragraph", 40), policy, head="STATE\n\n")

        assert parts[0].body.startswith("STATE")
        assert all("STATE" not in p.body for p in parts[1:])

    def test_first_part_leaves_room_for_the_head(self):
        policy = CommentSizePolicy(max_comment_chars=200, max_parts=10)
        head = "H" * 50 + "\n\n"
        parts = split_comment_body(_lines("paragraph", 40), policy, head=head)

        assert all(len(p.body) <= 200 for p in parts)


class TestOverflow:
    def test_body_over_max_parts_folds_into_last_with_marker(self):
        policy = CommentSizePolicy(max_comment_chars=200, max_parts=2)
        parts = split_comment_body(_lines("paragraph", 80), policy)

        assert len(parts) == 2
        assert "truncated due to size limit" in parts[-1].body
        assert all(len(p.body) <= 200 for p in parts)

    def test_overflow_keeps_the_head_on_the_first_part(self):
        policy = CommentSizePolicy(max_comment_chars=200, max_parts=2)
        parts = split_comment_body(_lines("paragraph", 80), policy, head="STATE\n\n")

        assert parts[0].body.startswith("STATE")
        assert "STATE" not in parts[-1].body
