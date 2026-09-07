"""Resolve configuration package.

Package-by-feature DDD layout:
  sub_domains/      -- domain value objects (TriggerCommands)
  features/         -- behavioral features (parse_trigger, fetch_state,
                       resolve_route, build_result)
  resolve_config.py -- facade (resolve_config() orchestrator + main() CLI)
"""

# Facade
from .resolve_config import resolve_config, main

# Domain value object
from .sub_domains.trigger_commands.trigger_commands import TriggerCommands

# Public extraction helpers (backward compat for the old flat module surface)
from .features.parse_trigger.parse_trigger import (
    is_bot_comment,
    has_universal_bot_marker,
    is_plain_mention_trigger,
    extract_mention_agent,
    extract_workflow_command,
    extract_force_next_command,
    extract_resume_command,
    normalize_resume_input,
    BARE_RESUME_INPUT,
    extract_debug_flag,
    _parse_trigger_body,
    build_trigger_commands,
)

# Routing helpers (backward compat for tests)
from .features.resolve_route.resolve_route import (
    resolve_next_agent_from_workflow,
    count_consecutive_same_agent,
    MAX_LOOP_COUNT,
)

# Exposed at package level so patch("resolve_config.get_issue_comments") in
# the test suite resolves here, exactly like the old flat module did.
from github_client import get_issue_comments
from .features.fetch_state.fetch_state import (
    _fetch_workflow_state_from_history,
)

__all__ = [
    "resolve_config",
    "main",
    "TriggerCommands",
    "is_bot_comment",
    "has_universal_bot_marker",
    "is_plain_mention_trigger",
    "extract_mention_agent",
    "extract_workflow_command",
    "extract_force_next_command",
    "extract_resume_command",
    "normalize_resume_input",
    "BARE_RESUME_INPUT",
    "extract_debug_flag",
    "_parse_trigger_body",
    "build_trigger_commands",
    "resolve_next_agent_from_workflow",
    "count_consecutive_same_agent",
    "MAX_LOOP_COUNT",
    "_fetch_workflow_state_from_history",
    "get_issue_comments",
]