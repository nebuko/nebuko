"""Facade for agent_script package — DDD Package-by-Feature."""

# Sub-domain re-exports
from .sub_domains.message_splitter.message_splitter import (
    split_message,
    _split_at_boundaries,
    _split_at_sentence_boundaries,
)

# Feature re-exports for main() consumption
from .features.load_prompt.load_prompt import load_prompt
from .features.collect_metrics.collect_metrics import collect_and_save_metrics
from .features.resolve_llm_config.resolve_llm_config import resolve_llm_config
from .features.run_agent.run_agent import run_agent
from .features.build_output.build_output import (
    build_output,
    load_prev_state_or_recover,
)

__all__ = [
    "split_message",
    "_split_at_boundaries",
    "_split_at_sentence_boundaries",
    "load_prompt",
    "collect_and_save_metrics",
    "resolve_llm_config",
    "run_agent",
    "build_output",
    "load_prev_state_or_recover",
]
