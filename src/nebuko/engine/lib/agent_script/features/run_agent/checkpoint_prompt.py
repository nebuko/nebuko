"""Checkpoint prompts — what the agent is asked at each checkpoint (issue #966).

Each message asks for a *decision* (converging? narrow the scope or fold) and
for *concrete content* — files touched, commands run, the next three actions,
open questions — written through the ``handoff`` tool so ``plan_changes`` is
recorded. Never a bare "X minutes left" or a loop-count estimate: those tend
to cause rushing instead of a useful checkpoint.
"""

CHECKPOINT_PROMPTS: tuple[str, str] = ("", "")  # @stub-protected — checkpoints 1 and 2
FINAL_HANDOFF_PROMPT: str = ""  # @stub-protected — checkpoint 3, simplified WIND_DOWN_PROMPT


# @stub-protected: signature defined by architect — do not modify
def checkpoint_prompt(index: int) -> str:  # @stub-protected
    """1-based index; 1..2 -> CHECKPOINT_PROMPTS[index-1]; 3 -> FINAL_HANDOFF_PROMPT."""
    raise NotImplementedError
