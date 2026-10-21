"""Tests for the bounded publication-recall loop (publication guard).

Mirrors ``test_dispatch_recall``: the loop is driven through injected doubles
(no LLM, no event loop), covering recall-then-published, the hard cap, the
budget break and a dead conversation.
"""

import unittest

from agent_script.features.run_agent.publication_guard import (
    MAX_PUBLICATION_RECALLS,
    perform_publication_recalls,
    publication_recall_prompt,
)


class _FakeConversation:
    def __init__(self, fail_send=False):
        self.sent_messages = []
        self._fail_send = fail_send

    def send_message(self, prompt):
        if self._fail_send:
            raise RuntimeError("conversation closed")
        self.sent_messages.append(prompt)


class TestPublicationRecallPrompt(unittest.TestCase):
    def test_names_every_reason(self):
        prompt = publication_recall_prompt(
            ["dirty_tree", "unpushed_commits", "missing_pr"]
        )
        self.assertIn("uncommitted changes", prompt)
        self.assertIn("not pushed", prompt)
        self.assertIn("no pull request", prompt)

    def test_teaches_the_full_workflow(self):
        """The agent keeps control of branch, commit message and PR body."""
        prompt = publication_recall_prompt(["dirty_tree"])
        for token in ("Commit", "Push", "pull request", "git-pr-workflow", "finalize"):
            self.assertIn(token, prompt)

    def test_unknown_reason_is_passed_through(self):
        self.assertIn("mystery", publication_recall_prompt(["mystery"]))


class TestPerformPublicationRecalls(unittest.TestCase):
    def test_no_recall_when_already_published(self):
        conv = _FakeConversation()
        count = perform_publication_recalls(conv, lambda: (), lambda n: True)
        self.assertEqual(count, 0)
        self.assertEqual(conv.sent_messages, [])

    def test_recall_then_published_stops_after_first_turn(self):
        conv = _FakeConversation()
        state = {"reasons": ("dirty_tree",)}

        def run_pass(n):
            # The agent complied: commit + push + PR happened during the turn.
            state["reasons"] = ()
            return True

        count = perform_publication_recalls(
            conv, lambda: state["reasons"], run_pass
        )
        self.assertEqual(count, 1)
        self.assertEqual(len(conv.sent_messages), 1)
        self.assertIn("UNPUBLISHED WORK", conv.sent_messages[0])

    def test_exhausted_recalls_hit_the_hard_cap(self):
        """An agent ignoring every nudge cannot loop: exactly the cap."""
        conv = _FakeConversation()
        count = perform_publication_recalls(
            conv, lambda: ("dirty_tree",), lambda n: True
        )
        self.assertEqual(count, MAX_PUBLICATION_RECALLS)
        self.assertEqual(len(conv.sent_messages), MAX_PUBLICATION_RECALLS)

    def test_dead_conversation_degrades_gracefully(self):
        conv = _FakeConversation(fail_send=True)
        count = perform_publication_recalls(
            conv, lambda: ("dirty_tree",), lambda n: True
        )
        self.assertEqual(count, 1)
        self.assertEqual(conv.sent_messages, [])

    def test_budget_exhausted_pass_stops_early(self):
        conv = _FakeConversation()
        count = perform_publication_recalls(
            conv, lambda: ("missing_pr",), lambda n: False
        )
        self.assertEqual(count, 1)
        self.assertEqual(len(conv.sent_messages), 1)


if __name__ == "__main__":
    unittest.main()
