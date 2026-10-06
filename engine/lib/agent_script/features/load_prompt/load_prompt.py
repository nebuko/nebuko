"""Load prompt feature — thin wrapper re-exporting from prompt sub-domain."""

from agent_script.sub_domains.prompt.prompt import is_url, load_prompt

__all__ = ["is_url", "load_prompt"]
