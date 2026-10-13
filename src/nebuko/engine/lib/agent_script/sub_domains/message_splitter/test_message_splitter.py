"""Tests for multi-comment answer splitting (issue #913)."""

from agent_script.sub_domains.message_splitter.message_splitter import (
    CommentPart,
    split_comment_body,
)
from config import CommentSizePolicy


def _lines(text: str, count: int) -> str:
    """``count`` paragraph-separated lines, each safely under a small budget."""
    return "\n\n".join(f"{text} {i}" for i in range(count))


class TestSinglePart:
    def test_short_body_posts_as_one_headerless_part(self):
        parts = split_comment_body("hello world", CommentSizePolicy())

        assert parts == [CommentPart(index=1, total=1, body="hello world")]

    def test_tail_is_appended_without_a_header(self):
        parts = split_comment_body("body", CommentSizePolicy(), tail="\nSTATE")

        assert len(parts) == 1
        assert parts[0].body == "body\nSTATE"
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
            assert part.body.startswith(f"*(Part {part.index}/{part.total})*\n\n")

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
        body = "\n\n".join(["A" * 80, "B" * 80, "C" * 80])
        parts = split_comment_body(body, CommentSizePolicy(max_comment_chars=120, max_parts=10))

        assert parts[0].body.endswith("A" * 80)
        assert "B" * 80 in parts[1].body


class TestTailReservation:
    def test_tail_lands_only_on_the_last_part(self):
        policy = CommentSizePolicy(max_comment_chars=200, max_parts=10)
        parts = split_comment_body(_lines("paragraph", 40), policy, tail="\n\nSTATE")

        assert "STATE" not in parts[0].body
        assert parts[-1].body.endswith("STATE")

    def test_last_part_leaves_room_for_the_tail(self):
        policy = CommentSizePolicy(max_comment_chars=200, max_parts=10)
        tail = "\n\n" + "T" * 50
        parts = split_comment_body(_lines("paragraph", 40), policy, tail=tail)

        assert all(len(p.body) <= 200 for p in parts)


class TestOverflow:
    def test_body_over_max_parts_folds_into_last_with_marker(self):
        policy = CommentSizePolicy(max_comment_chars=200, max_parts=2)
        parts = split_comment_body(_lines("paragraph", 80), policy)

        assert len(parts) == 2
        assert "truncated due to size limit" in parts[-1].body
        assert all(len(p.body) <= 200 for p in parts)

    def test_overflow_keeps_the_tail_on_the_last_part(self):
        policy = CommentSizePolicy(max_comment_chars=200, max_parts=2)
        parts = split_comment_body(_lines("paragraph", 80), policy, tail="\nSTATE")

        assert parts[-1].body.rstrip().endswith("STATE")
        assert "STATE" not in parts[0].body
