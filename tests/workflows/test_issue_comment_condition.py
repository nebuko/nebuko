"""Evaluates the ``issue_comment`` trigger condition of the reusable workflow.

Issue #1459: a human-association author's marker-led engine comment that
carries ``"function_call"`` (a recorded hand-off) woke the org run. The
condition is evaluated here with a small evaluator for the GitHub expression
subset the workflow uses, so the cases run against the real ``if:`` text.

The evaluator follows GitHub semantics where the condition depends on them:
``&&`` binds tighter than ``||``, ``!`` is unary and binds tightest, and
``contains`` / ``startsWith`` / ``==`` compare strings case-insensitively.
The evaluator itself is pinned by ``TestExpressionEvaluator``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from nebuko.engine.lib.prepare_comment import BOT_MARKER, CONTINUE_TOKEN

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"

_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<str>'(?:[^']|'')*')
      | (?P<op>&&|\|\||==|!=|[!(),])
      | (?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)
    )""",
    re.VERBOSE,
)


_LITERALS: dict[str, Any] = {"true": True, "false": False, "null": None}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value != ""
    return bool(value)


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.lower() == right.lower()
    return left == right


def _call(name: str, args: list[Any]) -> Any:
    if name == "fromJSON":
        return json.loads(args[0])
    if name in ("contains", "startsWith"):
        haystack, needle = args
        if name == "contains" and isinstance(haystack, list):
            return any(_equal(item, needle) for item in haystack)
        hay = str(haystack or "").lower()
        ndl = str(needle or "").lower()
        return ndl in hay if name == "contains" else hay.startswith(ndl)
    raise ValueError(f"unsupported function: {name}")


class _Parser:
    """Recursive descent over ``|| < && < == / != < ! < primary``."""

    def __init__(self, text: str, context: dict[str, Any]) -> None:
        self._tokens: list[tuple[str, str]] = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            match = _TOKEN_RE.match(text, pos)
            if not match or match.end() == pos:
                if text[pos:].strip() == "":
                    break
                raise ValueError(f"cannot tokenize at {text[pos:]!r}")
            kind = match.lastgroup
            self._tokens.append((kind, match.group(kind)))
            pos = match.end()
        self._pos = 0
        self._context = context

    def parse(self) -> Any:
        value = self._or()
        if self._pos != len(self._tokens):
            raise ValueError(f"trailing tokens: {self._tokens[self._pos:]}")
        return value

    def _peek_op(self, op: str) -> bool:
        return self._pos < len(self._tokens) and self._tokens[self._pos] == ("op", op)

    def _accept(self, op: str) -> bool:
        if self._peek_op(op):
            self._pos += 1
            return True
        return False

    def _expect(self, op: str) -> None:
        if not self._accept(op):
            raise ValueError(f"expected {op!r} at token {self._pos}")

    def _or(self) -> Any:
        value = self._and()
        while self._accept("||"):
            rhs = self._and()
            value = value if _truthy(value) else rhs
        return value

    def _and(self) -> Any:
        value = self._compare()
        while self._accept("&&"):
            rhs = self._compare()
            value = rhs if _truthy(value) else value
        return value

    def _compare(self) -> Any:
        left = self._unary()
        while True:
            if self._accept("=="):
                left = _equal(left, self._unary())
            elif self._accept("!="):
                left = not _equal(left, self._unary())
            else:
                return left

    def _unary(self) -> Any:
        if self._accept("!"):
            return not _truthy(self._unary())
        return self._primary()

    def _primary(self) -> Any:
        if self._pos >= len(self._tokens):
            raise ValueError("unexpected end of expression")
        kind, text = self._tokens[self._pos]
        self._pos += 1
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if (kind, text) == ("op", "("):
            value = self._or()
            self._expect(")")
            return value
        if kind == "name":
            if text in _LITERALS:
                return _LITERALS[text]
            if self._accept("("):
                args = [self._or()]
                while self._accept(","):
                    args.append(self._or())
                self._expect(")")
                return _call(text, args)
            if text not in self._context:
                raise KeyError(f"unknown context value: {text}")
            return self._context[text]
        raise ValueError(f"unexpected token {text!r}")


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """Evaluate a GitHub Actions expression (``${{ }}`` wrapper optional)."""
    text = expression.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    return _Parser(text, context).parse()


def _comment_context(
    body: str,
    association: str = "MEMBER",
    user_type: str = "User",
    login: str = "olivier-norture",
) -> dict[str, Any]:
    return {
        "github.event_name": "issue_comment",
        "github.event.action": "created",
        "github.event.comment.body": body,
        "github.event.comment.author_association": association,
        "github.event.comment.user.type": user_type,
        "github.event.comment.user.login": login,
        "github.event.issue.body": "",
        "github.event.issue.author_association": association,
        "inputs.CALLER_AUTH": "on",
        "inputs.TRUSTED_BOTS": None,
    }


def _run_task_condition() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return str(workflow["jobs"]["run-task"]["if"])


# Shapes mirror the live comments: an engine state comment opens with the
# universal bot marker, then the agent marker, then a collapsed state block
# whose JSON carries the hand-off key and no continue token.
AGENT_MARKER = "<!-- bot-agent-architect -->"
STATE_COMMENT = (
    f"{BOT_MARKER}\n"
    f"{AGENT_MARKER}\n"
    "<details>\n<summary>Workflow state</summary>\n\n"
    "```json\n"
    '{"function_call": {"name": "context", "arguments": {}}, "step": "analyze"}\n'
    "```\n</details>"
)
HOLD_COMMENT = f"{BOT_MARKER}\n\nRun on hold: awaiting human input."
CONTINUED_COMMENT = f"{BOT_MARKER}{CONTINUE_TOKEN}\n{STATE_COMMENT}"
LEGACY_FUNCTION_CALL = '{"function_call": {"name": "context", "arguments": {}}}'
HUMAN_GATE = "/nebuko approve gate --feedback Approved as planned"
BOT_LOGIN = "nebuko[bot]"


class TestExpressionEvaluator:
    """Pins the evaluator so the condition cases cannot pass by accident."""

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("true || false && false", True),
            ("(true || false) && false", False),
            ("false && true || true", True),
            ("!false && true", True),
            ("!(false || false)", True),
            ("!true || true", True),
            ("'a' == 'A'", True),
            ("'a' != 'B'", True),
        ],
    )
    def test_precedence_parentheses_and_negation(self, expression, expected):
        assert evaluate(expression, {}) is expected

    def test_or_returns_first_truthy_operand(self):
        assert evaluate("inputs.TRUSTED_BOTS || 'fallback'", {"inputs.TRUSTED_BOTS": None}) == "fallback"

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("contains('Hello World', 'WORLD')", True),
            ("contains('Hello World', 'planet')", False),
            ("startsWith('<!-- BOT-WORKFLOW-TERMINAL --> x', '<!-- bot-workflow-terminal -->')", True),
            ("startsWith('x <!-- bot-workflow-terminal -->', '<!-- bot-workflow-terminal -->')", False),
            ("contains(fromJSON('[\"MEMBER\"]'), 'member')", True),
        ],
    )
    def test_string_functions_are_case_insensitive(self, expression, expected):
        assert evaluate(expression, {}) is expected

    def test_unknown_context_value_fails_loudly(self):
        with pytest.raises(KeyError):
            evaluate("github.event.comment.bodyy", {"github.event.comment.body": ""})


@pytest.fixture(scope="module")
def condition() -> str:
    return _run_task_condition()


class TestIssueCommentCondition:
    """Evaluates the real ``run-task`` ``if:`` against the comment shapes."""

    def _triggers(self, condition: str, body: str, **kwargs: Any) -> bool:
        return bool(evaluate(condition, _comment_context(body, **kwargs)))

    def test_marker_led_state_comment_with_function_call_does_not_trigger(self, condition):
        # Case (a): the comment that woke the org run on #1414.
        assert self._triggers(condition, STATE_COMMENT) is False

    def test_hold_turn_comment_does_not_trigger(self, condition):
        assert self._triggers(condition, HOLD_COMMENT) is False

    def test_marker_plus_continue_token_triggers(self, condition):
        # Case (b): org flows relying on the continue token are unchanged.
        assert self._triggers(condition, CONTINUED_COMMENT) is True

    def test_legacy_function_call_without_marker_triggers(self, condition):
        # Case (c).
        assert self._triggers(condition, LEGACY_FUNCTION_CALL) is True

    def test_human_gate_approval_still_triggers(self, condition):
        assert self._triggers(condition, HUMAN_GATE) is True
        assert self._triggers(condition, HUMAN_GATE, association="COLLABORATOR") is True

    def test_human_mention_still_triggers(self, condition):
        assert self._triggers(condition, "@@architect please re-plan") is True

    def test_non_member_comment_does_not_trigger(self, condition):
        assert self._triggers(condition, HUMAN_GATE, association="NONE") is False

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            # Case (d): bot author branch is unchanged.
            (STATE_COMMENT, False),
            (CONTINUED_COMMENT, True),
            (LEGACY_FUNCTION_CALL, True),
        ],
    )
    def test_bot_author_branch_unchanged(self, condition, body, expected):
        assert (
            self._triggers(condition, body, association="NONE", user_type="Bot", login=BOT_LOGIN)
            is expected
        )
