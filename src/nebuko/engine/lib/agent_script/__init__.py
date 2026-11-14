"""Facade for agent_script package — DDD Package-by-Feature.

Re-exports resolve lazily (:pep:`562`) so importing *any* submodule — notably
the base-deps-safe ``sub_domains.metrics`` modules the summarize metrics job
needs — does not drag in ``openhands``/``litellm``, which ship only with the
``runtime`` extra (issue #928). The public surface is unchanged: attribute
access (``agent_script.run_agent``) and ``from agent_script import run_agent``
both resolve through ``__getattr__``.
"""

from importlib import import_module

#: Public attribute name -> module (relative) that defines it.
_EXPORTS: dict[str, str] = {
    "split_message": ".sub_domains.message_splitter.message_splitter",
    "_split_at_boundaries": ".sub_domains.message_splitter.message_splitter",
    "_split_at_sentence_boundaries": ".sub_domains.message_splitter.message_splitter",
    "load_prompt": ".features.load_prompt.load_prompt",
    "collect_and_save_metrics": ".features.collect_metrics.collect_metrics",
    "resolve_llm_config": ".features.resolve_llm_config.resolve_llm_config",
    "LLMConfigError": ".features.resolve_llm_config.resolve_llm_config",
    "run_agent": ".features.run_agent.run_agent",
    "build_output": ".features.build_output.build_output",
    "load_prev_state_or_recover": ".features.build_output.build_output",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_path, __name__), name)
    globals()[name] = value  # cache: later lookups bypass __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
