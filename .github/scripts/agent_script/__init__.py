"""Facade for agent_script package — DDD Package-by-Feature."""

# Sub-domain re-exports (backward compatibility for test_split_message.py)
from .sub_domains.message_splitter.message_splitter import (
    split_message,
    _split_at_boundaries,
    _split_at_sentence_boundaries,
)
from .sub_domains.split_plan.split_plan import (
    extract_split_plan,
    strip_split_plan,
    split_plan_summary,
)

# Feature re-exports for main() consumption
from .features.load_prompt.load_prompt import load_prompt
from .features.collect_metrics.collect_metrics import collect_and_save_metrics
from .features.extract_split_plan.extract_split_plan import (
    extract_and_strip_split_plan,
    generate_split_plan_summary,
)
from .features.resolve_llm_config.resolve_llm_config import resolve_llm_config
from .features.run_agent.run_agent import run_agent
from .features.build_output.build_output import build_output

__all__ = [
    "split_message",
    "_split_at_boundaries",
    "_split_at_sentence_boundaries",
    "extract_split_plan",
    "strip_split_plan",
    "split_plan_summary",
    "load_prompt",
    "collect_and_save_metrics",
    "extract_and_strip_split_plan",
    "generate_split_plan_summary",
    "resolve_llm_config",
    "run_agent",
    "build_output",
]
