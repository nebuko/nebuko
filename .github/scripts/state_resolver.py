#!/usr/bin/env python3
"""
State resolver module.

Consolidates all state retrieval logic into a single class with a clear
precedence chain: explicit override > trigger body > environment JSON >
comment history > default empty state.

Replaces scattered state extraction from prepare-state YAML inline Python,
get_last_state.py, and other locations.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from workflow_state.workflow_state import WorkflowState
from workflow_state.features.get_last_state.get_last_state import (
    extract_state_from_body,
    extract_latest_state_from_comments,
)

logger = logging.getLogger(__name__)


class StateResolver:
    """Resolves the current WorkflowState from available sources.

    Provides a single entry point for state resolution with a well-defined
    precedence chain. Each source is tried in order; the first match wins.
    """

    def resolve(
        self,
        trigger: str = "",
        comment_history: Optional[list[dict]] = None,
        explicit_state_json: Optional[str] = None,
    ) -> WorkflowState:
        """Resolve the current WorkflowState.

        Precedence order:
        1. explicit_state_json — caller-provided JSON string
        2. trigger body — embedded <!-- WORKFLOW_STATE_START --> markers
        3. comment history — search backwards through comments
        4. default — empty WorkflowState

        Args:
            trigger: The trigger message body text.
            comment_history: List of comment dicts from GitHub API.
            explicit_state_json: JSON string of a WorkflowState.

        Returns:
            A WorkflowState instance (never None).
        """
        # Source 1: Explicit JSON override (highest priority)
        if explicit_state_json:
            state = self._from_json_string(explicit_state_json)
            if state:
                logger.info("Resolved state from explicit_state_json")
                return state

        # Source 2: Embedded in trigger body
        state = extract_state_from_body(trigger)
        if state:
            logger.info("Resolved state from trigger body")
            return state

        # Source 3: Comment history
        if comment_history:
            state = extract_latest_state_from_comments(comment_history)
            if state:
                logger.info("Resolved state from comment history")
                return state

        # Source 4: Default empty state
        logger.info("No state found — returning default empty WorkflowState")
        return WorkflowState()

    @staticmethod
    def _from_json_string(json_str: str) -> Optional[WorkflowState]:
        """Parse a JSON string into a WorkflowState.

        Args:
            json_str: JSON string representation of WorkflowState.

        Returns:
            Parsed WorkflowState or None on failure.
        """
        try:
            data = json.loads(json_str)
            return WorkflowState.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to parse explicit_state_json: {e}")
            return None


def main():
    """CLI entry point for state resolution.

    Usage:
        python state_resolver.py --trigger <text> [--prev-state-json <json>] [--output-file <path>]
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Resolve workflow state from available sources")
    parser.add_argument("--trigger", type=str, default="", help="Trigger message body text")
    parser.add_argument("--prev-state-json", type=str, default="", help="Previous workflow state JSON string")
    parser.add_argument("--output-file", type=str, default="", help="Output file path for workflow_state.json")

    args = parser.parse_args()

    resolver = StateResolver()
    state = resolver.resolve(
        trigger=args.trigger,
        explicit_state_json=args.prev_state_json or None,
    )

    output_json = json.dumps(state.model_dump())

    if args.output_file:
        with open(args.output_file, "w") as f:
            f.write(output_json)
        logger.info(f"Wrote workflow_state.json to {args.output_file}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
