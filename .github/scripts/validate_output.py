"""
Output validation utilities for agent outputs.

Validates agentRunConfig blocks and checks for forbidden sections
to improve instruction-following compliance.
"""

import json
import re
import sys
from dataclasses import dataclass
from typing import Optional

# Pattern to extract agentRunConfig JSON blocks from text
AGENT_RUN_CONFIG_PATTERN = re.compile(
    r"<agentRunConfig>\s*(.*?)\s*</agentRunConfig>",
    re.DOTALL,
)

# Forbidden section headers that ctx agent must never include
CTX_FORBIDDEN_SECTIONS = [
    "ROOT CAUSE",
    "ANALYSIS",
    "FIX",
    "SOLUTION",
    "RECOMMENDATION",
    "PROPOSED CHANGES",
    "TWO POSSIBLE FIXES",
]


@dataclass
class ValidationResult:
    """Result of validating an agent output."""

    is_valid: bool
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def add_error(self, message: str):
        self.errors.append(message)
        self.is_valid = False


MAX_RETRIES = 2


def extract_agent_run_configs(text: str) -> list[str]:
    """Extract all agentRunConfig JSON strings from text."""
    matches = AGENT_RUN_CONFIG_PATTERN.findall(text)
    return [match.strip() for match in matches if match.strip()]


def should_retry_agent(comment_body: str, role: str, retry_count: int = 0) -> dict:
    """Determine if an agent comment is missing its expected <agentRunConfig>.

    All agents are expected to produce an agentRunConfig block — either a real
    hand-off task or ``wait_for_human``.  If none is found the agent should be
    retried (up to *MAX_RETRIES* times), after which we escalate to genius.

    Args:
        comment_body: The posted agent comment.
        role: Agent role name (e.g. "dev", "ctx").
        retry_count: How many times this agent has already been retried.

    Returns:
        Dict with keys RETRY (true/false), optional AGENT, and optional ESCALATE.
    """
    configs = extract_agent_run_configs(comment_body)

    if configs:
        # Has at least one agentRunConfig — check whether it signals intentional stop
        for config_str in configs:
            try:
                config = json.loads(config_str)
                if isinstance(config, dict) and config.get("task") == "wait_for_human":
                    return {"RETRY": "false"}
            except (json.JSONDecodeError, TypeError):
                pass
        # Has valid hand-off (non-wait_for_human) → no retry needed
        return {"RETRY": "false"}

    # No agentRunConfig found — retry or escalate
    if retry_count >= MAX_RETRIES:
        return {
            "RETRY": "true",
            "AGENT": "genius",
            "ESCALATE": "true",
        }

    return {
        "RETRY": "true",
        "AGENT": role,
        "ESCALATE": "false",
    }


def validate_agent_run_config(json_str: str) -> ValidationResult:
    """Validate a single agentRunConfig JSON block."""
    result = ValidationResult(is_valid=True)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        result.add_error(f"Invalid JSON in agentRunConfig: {e}")
        return result

    if not isinstance(data, dict):
        result.add_error("agentRunConfig must be a JSON object")
        return result

    # task is required
    if "task" not in data:
        result.add_error("agentRunConfig missing required field: 'task'")

    # Validate known fields' types
    valid_tasks = {"genius", "ctx", "dev", "review", "issue", "issue-reviewer", "wait_for_human"}
    if data.get("task") and data["task"] not in valid_tasks:
        result.add_error(
            f"Invalid task '{data['task']}'. Must be one of: {valid_tasks}"
        )

    valid_complexities = {"low", "medium", "high"}
    if data.get("complexity") and data["complexity"] not in valid_complexities:
        result.add_error(
            f"Invalid complexity '{data['complexity']}'. "
            f"Must be one of: {valid_complexities}"
        )

    valid_reasoning = {"none", "low", "medium", "high"}
    if data.get("reasoning") and data["reasoning"] not in valid_reasoning:
        result.add_error(
            f"Invalid reasoning '{data['reasoning']}'. "
            f"Must be one of: {valid_reasoning}"
        )

    # Validate numeric fields
    for field in ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty"):
        if data.get(field) is not None:
            val = data[field]
            if not isinstance(val, (int, float)):
                result.add_error(f"'{field}' must be a number, got {type(val).__name__}")
            elif field == "top_p" and not (0 <= val <= 1):
                result.add_error(f"'{field}' must be in range [0, 1], got {val}")
            elif field == "min_p" and not (0 <= val <= 1):
                result.add_error(f"'{field}' must be in range [0, 1], got {val}")
            elif field == "presence_penalty" and not (-2.0 <= val <= 2.0):
                result.add_error(f"'{field}' must be in range [-2.0, 2.0], got {val}")
            elif field in ("top_k", "repetition_penalty") and val <= 0:
                result.add_error(f"'{field}' must be positive, got {val}")

    if data.get("round") is not None and not isinstance(data["round"], int):
        result.add_error("'round' must be an integer")

    return result


def validate_forbidden_sections(
    text: str, forbidden_headers: Optional[list[str]] = None
) -> ValidationResult:
    """Check if text contains forbidden section headers.

    Matches section headers (e.g. ``## ROOT CAUSE``, ``### FIX``) rather
    than arbitrary substrings, to avoid false positives like "RESOLUTION"
    matching "SOLUTION".

    Args:
        text: The agent output text to check.
        forbidden_headers: List of section headers to forbid. Defaults to CTX_FORBIDDEN_SECTIONS.

    Returns:
        ValidationResult with is_valid=False if any forbidden headers are found.
    """
    result = ValidationResult(is_valid=True)
    if forbidden_headers is None:
        forbidden_headers = CTX_FORBIDDEN_SECTIONS

    for header in forbidden_headers:
        # Build a case-insensitive pattern that matches the header as a
        # standalone section heading (preceded by ##/### or start of line).
        pattern = re.compile(
            r"(^|\n)\s*#{1,6}\s*" + re.escape(header) + r"\b",
            re.IGNORECASE,
        )
        if pattern.search(text):
            result.add_error(f"Found forbidden section header: '{header}'")

    return result


def validate_agent_output(
    text: str,
    role: str = "",
) -> ValidationResult:
    """Validate full agent output based on role-specific rules.

    Args:
        text: The agent output text.
        role: The agent role name (e.g., "ctx", "dev").

    Returns:
        ValidationResult containing all found issues.
    """
    result = ValidationResult(is_valid=True)

    # Validate all agentRunConfig blocks
    configs = extract_agent_run_configs(text)
    if configs:
        for i, config_str in enumerate(configs):
            config_result = validate_agent_run_config(config_str)
            for error in config_result.errors:
                result.add_error(f"agentRunConfig[{i}]: {error}")

    # Role-specific validation
    if role == "ctx":
        forbidden_result = validate_forbidden_sections(text)
        for error in forbidden_result.errors:
            result.add_error(error)

    return result


def check_retry(comment_file: str, role: str, retry_count: int = 0) -> dict:
    """CLI subcommand: check-retry.

    Reads a comment file and determines whether the agent should be retried.

    Args:
        comment_file: Path to the comment file.
        role: Agent role name.
        retry_count: Number of previous retries.

    Returns:
        Dict with RETRY=true/false, optional AGENT and ESCALATE.
    """
    with open(comment_file, "r", encoding="utf-8") as f:
        text = f.read()
    return should_retry_agent(text, role, retry_count)


def main():
    """CLI entry point: validate output or check-retry from file."""
    if len(sys.argv) < 2:
        print("Usage: python validate_output.py <command> [args]")
        print("Commands:")
        print("  validate <file> [role]           Validate output for issues")
        print("  check-retry --comment-file <f> --role <r> [--retry-count <n>]")
        print("                                   Check if agent should be retried")
        sys.exit(1)

    command = sys.argv[1]

    if command == "check-retry":
        import argparse

        parser = argparse.ArgumentParser(description="Check retry decision")
        parser.add_argument("--comment-file", required=True, help="Path to comment file")
        parser.add_argument("--role", required=True, help="Agent role name")
        parser.add_argument(
            "--retry-count", type=int, default=0, help="Current retry count"
        )
        args = parser.parse_args(sys.argv[2:])

        result = check_retry(args.comment_file, args.role, args.retry_count)

        for key, value in result.items():
            print(f"{key}={value}")
        sys.exit(0)

    if command == "validate" or command.endswith(".md") or command.endswith(".txt"):
        # Backward compat: first arg is file path
        filepath = command
        role = sys.argv[2] if len(sys.argv) > 2 else ""
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    result = validate_agent_output(text, role)

    if result.is_valid:
        print("VALID: No issues found")
        sys.exit(0)
    else:
        print(f"INVALID: {len(result.errors)} issue(s) found:")
        for error in result.errors:
            print(f"  - {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
