"""The project caller template must not start a run for a human --personal comment (#1479).

The job condition is a GitHub expression. The evaluator below covers the subset the
template uses, so the shared command samples can be run through the real condition
text instead of string checks on it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CALLER = REPO / "src/nebuko/engine/init/defaults/nebuko-caller.yaml"
SAMPLES = json.loads((REPO / "tests/fixtures/personal-command-samples.json").read_text(encoding="utf-8"))["samples"]


def _condition() -> str:
    text = CALLER.read_text(encoding="utf-8")
    return text.split("if: >-", 1)[1].split("uses:", 1)[0]


_TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(&&|\|\||==|!=|[()!,])|([A-Za-z_][\w.\[\]]*))")


def _tokens(text: str) -> list[str]:
    pos, out = 0, []
    text = text.strip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise ValueError(f"cannot tokenize at {text[pos:pos + 20]!r}")
        out.append(match.group(0).strip())
        pos = match.end()
    return out


def _evaluate(expression: str, context: dict) -> object:
    """Evaluate a GitHub expression with GitHub's case-insensitive string semantics."""
    tokens = _tokens(expression)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def value_of(token):
        if token.startswith("'"):
            return token[1:-1].replace("''", "'")
        raise ValueError(token)

    def fold(v):
        return v.lower() if isinstance(v, str) else v

    def primary():
        token = take()
        if token == "(":
            inner = or_expr()
            assert take() == ")"
            return inner
        if token == "!":
            return not truthy(primary())
        if token.startswith("'"):
            return value_of(token)
        if peek() == "(":
            take()
            args = [or_expr()]
            while peek() == ",":
                take()
                args.append(or_expr())
            assert take() == ")"
            return call(token, args)
        return context[token]

    def call(name, args):
        if name == "fromJSON":
            return json.loads(args[0])
        if name == "contains":
            haystack, needle = args
            if isinstance(haystack, list):
                return any(fold(item) == fold(needle) for item in haystack)
            return fold(needle) in fold(haystack)
        if name == "startsWith":
            return fold(args[0]).startswith(fold(args[1]))
        raise ValueError(name)

    def truthy(v):
        return bool(v)

    def comparison():
        left = primary()
        if peek() in ("==", "!="):
            op = take()
            right = primary()
            equal = fold(left) == fold(right)
            return equal if op == "==" else not equal
        return left

    def and_expr():
        value = comparison()
        while peek() == "&&":
            take()
            right = comparison()
            value = truthy(value) and truthy(right)
        return value

    def or_expr():
        value = and_expr()
        while peek() == "||":
            take()
            right = and_expr()
            value = truthy(value) or truthy(right)
        return value

    result = or_expr()
    assert pos == len(tokens), "trailing tokens"
    return result


def _runs(body: str, *, login: str = "alice", association: str = "OWNER") -> bool:
    """True when the caller job starts for this comment."""
    context = {
        "github.event_name": "issue_comment",
        "github.event.comment.body": body,
        "github.event.comment.user.login": login,
        "github.event.comment.author_association": association,
    }
    return bool(_evaluate(_condition(), context))


def test_caller_job_condition_skips_personal_comments_by_exact_shape():
    condition = _condition()
    assert "github.event_name != 'issue_comment' ||" in condition
    assert "!contains(fromJSON(" in condition
    # Bot comments are exempt, so a bot body that mentions --personal still passes.
    assert condition.index("github.event.comment.user.login == 'nebuko[bot]' ||") < condition.index(
        "!contains(fromJSON("
    )


@pytest.mark.parametrize("sample", [s for s in SAMPLES if s["class"] == "project run"], ids=lambda s: s["name"])
def test_condition_never_skips_a_project_run_row(sample):
    assert _runs(sample["body"]), f"caller skips a project run: {sample['body']!r}"


@pytest.mark.parametrize(
    "body",
    [
        "/nebuko run workflow --personal",
        "/nebuko approve gate --personal",
        "/nebuko reject gate --personal",
        "/nebuko resume --personal",
    ],
)
def test_exact_one_flag_shapes_are_skipped(body):
    assert not _runs(body)


def test_shapes_beyond_the_exact_list_start_a_runner():
    # Decided by the engine's skip; the expression only skips what it can decide.
    assert _runs("/nebuko run workflow --name default --personal")
    assert _runs("/nebuko resume --personal\nthanks")


@pytest.mark.parametrize("login", ["nebuko[bot]"])
def test_bot_comments_always_start_the_job(login):
    assert _runs("/nebuko approve gate --personal", login=login, association="NONE")


def test_non_member_human_comment_does_not_start_the_job():
    assert not _runs("/nebuko resume", association="NONE")


def test_push_events_are_not_filtered():
    context = {
        "github.event_name": "push",
        "github.event.comment.body": "/nebuko resume --personal",
        "github.event.comment.user.login": "alice",
        "github.event.comment.author_association": "NONE",
    }
    assert _evaluate(_condition(), context) is True
