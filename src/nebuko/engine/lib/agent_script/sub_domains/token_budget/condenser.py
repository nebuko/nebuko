"""Build an agent's condenser from its TokenBudgetPolicy (issue #851).

Approved policy (see ``config.../token_budget/token_budget.py``): compaction is a
*late* safety valve that runs on the role's own model. The event-count trigger
moves from the SDK preset's 80 events (which fires mid-run and breaks the prompt
cache) to 240, and an explicit token trigger fires at 80% of the effective input
limit — so the token valve is never left unset.

The summarizer runs on the role's own LLM so the summary request shares the
conversation's prompt cache; the copy only carries ``usage_id="condenser"`` so
its spend is attributed instead of vanishing into the conversation total.
"""

from openhands.sdk import LLM
from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    LLMSummarizingCondenser,
)

from config import TokenBudgetPolicy

#: Effective input limit used when the provider route exposes no static context
#: window (e.g. the self-hosted vLLM endpoint resolves ``None`` at construction).
#: Large enough that the token valve stays a late safety net.
DEFAULT_EFFECTIVE_INPUT_LIMIT = 128_000

#: Tag attached to the summarizer's LLM copy so its spend is metered separately.
CONDENSER_USAGE_ID = "condenser"


def resolve_token_budget_policy(agent_cfg) -> TokenBudgetPolicy:
    """The role's policy, or the documented defaults when no config is supplied."""
    policy = getattr(agent_cfg, "token_budget", None)
    return policy if isinstance(policy, TokenBudgetPolicy) else TokenBudgetPolicy()


def resolve_effective_input_limit(llm: LLM) -> int:
    """The input-token budget of the role's route.

    Uses the LLM's static/resolved route metadata when present; the local vLLM
    route exposes none, so a conservative default keeps the token valve active
    rather than silently disabled.
    """
    limit = llm.effective_max_input_tokens
    if limit is not None:
        return limit
    return DEFAULT_EFFECTIVE_INPUT_LIMIT


def build_condenser(llm: LLM, policy: TokenBudgetPolicy) -> LLMSummarizingCondenser:
    """Construct the role's condenser under the approved late-valve policy."""
    summarizer = llm.model_copy(
        update={
            "model": policy.resolved_compaction_model(llm.model),
            "usage_id": CONDENSER_USAGE_ID,
        }
    )
    return LLMSummarizingCondenser(
        llm=summarizer,
        max_size=policy.resolved_max_size(),
        max_tokens=policy.resolved_max_tokens(resolve_effective_input_limit(llm)),
        keep_first=policy.compaction_keep_first,
    )
