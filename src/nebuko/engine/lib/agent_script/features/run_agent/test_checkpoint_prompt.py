"""Tests for the checkpoint prompts (issue #966).

Each checkpoint message asks for a decision and concrete handoff content —
never a bare "X minutes left" or a loop-count estimate.
"""

import unittest

from agent_script.features.run_agent.checkpoint_prompt import (
    CHECKPOINT_PROMPTS,
    FINAL_HANDOFF_PROMPT,
    checkpoint_prompt,
)


class TestCheckpointPrompts(unittest.TestCase):
    """Given a checkpoint index, when the prompt is built, it asks for a decision + content."""

    def test_checkpoint_prompt_tuple_has_two_entries(self):
        self.assertEqual(len(CHECKPOINT_PROMPTS), 2)
        for prompt in CHECKPOINT_PROMPTS:
            self.assertIsInstance(prompt, str)
            self.assertGreater(len(prompt), 50)

    def test_final_handoff_prompt_is_non_empty(self):
        self.assertIsInstance(FINAL_HANDOFF_PROMPT, str)
        self.assertGreater(len(FINAL_HANDOFF_PROMPT), 50)

    def test_index_maps_to_prompts(self):
        self.assertEqual(checkpoint_prompt(1), CHECKPOINT_PROMPTS[0])
        self.assertEqual(checkpoint_prompt(2), CHECKPOINT_PROMPTS[1])
        self.assertEqual(checkpoint_prompt(3), FINAL_HANDOFF_PROMPT)

    def test_checkpoint_prompts_ask_for_a_decision(self):
        for prompt in (*CHECKPOINT_PROMPTS, FINAL_HANDOFF_PROMPT):
            lowered = prompt.lower()
            self.assertTrue(
                "converg" in lowered or "on track" in lowered,
                f"prompt asks for a convergence decision: {prompt[:80]}",
            )

    def test_checkpoint_prompts_ask_for_concrete_content(self):
        for prompt in CHECKPOINT_PROMPTS:
            lowered = prompt.lower()
            self.assertIn("handoff", lowered)
            self.assertTrue(
                "next" in lowered and "action" in lowered,
                f"prompt asks for next actions: {prompt[:80]}",
            )

    def test_checkpoint_prompts_never_give_time_remaining(self):
        for prompt in (*CHECKPOINT_PROMPTS, FINAL_HANDOFF_PROMPT):
            lowered = prompt.lower()
            self.assertNotIn("minutes left", lowered)
            self.assertNotIn("remaining time", lowered)

    def test_final_prompt_asks_for_handoff_then_commit_and_push(self):
        lowered = FINAL_HANDOFF_PROMPT.lower()
        self.assertIn("handoff", lowered)
        self.assertIn("commit", lowered)
        self.assertIn("push", lowered)


if __name__ == "__main__":
    unittest.main()
