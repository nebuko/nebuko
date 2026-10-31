"""Tests for the terminal command deny-list (engine-owned push, issue #1150).

Agents must never push: the engine owns the whole publish pipeline. These
tests pin both the deny-list matching rules and the wiring into the
``terminal_batch`` replay path (deny before execute, message + non-zero exit,
batch continues). They are expected to FAIL against the stubs.
"""

import re
import unittest
from unittest.mock import patch

from nebuko.engine.lib.agent_script.features.terminal_batch_tool.command_deny_list import (
    DenyResult,
    DenyRule,
    CommandDenyList,
    push_deny_list,
)
from nebuko.engine.lib.agent_script.features.terminal_batch_tool.terminal_batch_tool import (
    TerminalBatchAction,
    TerminalBatchExecutor,
    TerminalBatchTool,
)

DENY_MESSAGE = (
    "git push is disabled for agents: the engine commits leftovers, pushes "
    "the branch and opens the pull request after your run. Commit your work "
    "locally instead."
)


class TestPushDenyListMatching(unittest.TestCase):
    """The built-in `git push` rule: what must match and what must not."""

    def setUp(self):
        self.deny_list = push_deny_list()

    def test_plain_git_push_matches(self):
        result = self.deny_list.check("git push")
        self.assertIsNotNone(result)
        self.assertEqual(result.rule_name, "git-push")

    def test_git_push_with_refspec_matches(self):
        result = self.deny_list.check("git push origin main")
        self.assertIsNotNone(result)

    def test_git_push_with_global_option_matches(self):
        for statement in (
            "git -C repo push -f",
            "git --git-dir=.git push",
            "git --no-optional-locks push origin main",
        ):
            self.assertIsNotNone(self.deny_list.check(statement), statement)

    def test_leading_whitespace_and_env_assignment_still_match(self):
        self.assertIsNotNone(self.deny_list.check("  git push --all"))
        self.assertIsNotNone(self.deny_list.check("GIT_TRACE=1 git push"))

    def test_non_push_git_commands_do_not_match(self):
        for statement in (
            "git status",
            "git commit -m 'push it'",
            "git log origin/main",
        ):
            self.assertIsNone(self.deny_list.check(statement), statement)

    def test_piped_and_echoed_mentions_do_not_match(self):
        self.assertIsNone(self.deny_list.check("echo git push"))
        self.assertIsNone(self.deny_list.check("cat script.sh | grep push"))

    def test_message_explains_the_engine_owned_flow(self):
        result = self.deny_list.check("git push origin main")
        self.assertEqual(result.message, DENY_MESSAGE)

    def test_result_carries_the_statement_verbatim(self):
        raw = "  git push --all  "
        result = self.deny_list.check(raw)
        self.assertEqual(result.statement, raw)


class TestNoVerifyDenyListMatching(unittest.TestCase):
    """Agents may not skip the engine's workflow pre-commit hook."""

    def setUp(self):
        self.deny_list = push_deny_list()

    def _rule(self, statement):
        result = self.deny_list.check(statement)
        return result.rule_name if result else None

    def test_no_verify_flags_match(self):
        for statement in (
            "git commit --no-verify -m x",
            "git commit -m x --no-verify",
            "git commit -n -m x",
            "git commit -anm x",
            "git -C repo commit --no-verify -m x",
            "git add -A && git commit -nm x",
        ):
            self.assertEqual(self._rule(statement), "git-commit-no-verify", statement)

    def test_plain_commits_and_other_n_flags_do_not_match(self):
        for statement in (
            "git commit -m x",
            "git commit -am fix",
            'git commit -m "drop -n from the script"',
            "git log -n 3",
            "git status -n",
        ):
            self.assertIsNone(self._rule(statement), statement)

    def test_message_points_at_the_patch_flow(self):
        result = self.deny_list.check("git commit --no-verify -m x")
        self.assertIn(".github/workflows", result.message)


class TestCommandDenyListOrdering(unittest.TestCase):
    """Ordered rules; the first match wins."""

    def test_first_matching_rule_wins(self):
        first = DenyRule(
            name="first",
            pattern=re.compile(r"git push"),
            message="first message",
        )
        second = DenyRule(
            name="second",
            pattern=re.compile(r"git push"),
            message="second message",
        )
        result = CommandDenyList([first, second]).check("git push")
        self.assertEqual(result.rule_name, "first")

    def test_no_match_returns_none(self):
        rules = [
            DenyRule(name="x", pattern=re.compile(r"never-matches"), message="x")
        ]
        self.assertIsNone(CommandDenyList(rules).check("git status"))


class TestTerminalBatchDenyWiring(unittest.TestCase):
    """The executor denies matched statements before they reach the shell."""

    def test_denied_statement_is_not_executed_and_batch_continues(self):
        executor = TerminalBatchExecutor(executor=object())

        def fail(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("the denied statement reached the executor")

        with patch(
            "nebuko.engine.lib.agent_script.features.terminal_batch_tool.terminal_batch_tool."
            "TerminalExecutor",
            side_effect=fail,
        ):
            observation = executor(
                TerminalBatchAction(commands=["git status", "git push", "git status"]),
                conversation=None,
            )

        self.assertEqual(observation.exit_codes[1], 1)
        self.assertIn(DENY_MESSAGE, observation.results[1])
        self.assertEqual(observation.failed_count, 1)
        self.assertEqual(len(observation.exit_codes), 3)

    def test_tool_instance_has_a_default_deny_list(self):
        tool = TerminalBatchTool.create()[0]
        self.assertIsInstance(tool.executor, TerminalBatchExecutor)
        self.assertIsInstance(tool.executor.deny_list, CommandDenyList)


if __name__ == "__main__":
    unittest.main()
