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


def extract_agent_run_configs(text: str) -> list[str]:
    """Extract all agentRunConfig JSON strings from text."""
    matches = AGENT_RUN_CONFIG_PATTERN.findall(text)
    return [match.strip() for match in matches if match.strip()]


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


VALID_DISPATCH_STATUSES = {"advance", "loop", "hold", "terminate"}


def validate_dispatch(data: dict) -> ValidationResult:
    """Validate a dispatch dict (from WorkflowState.dispatch).

    Args:
        data: Dict containing dispatch fields.

    Returns:
        ValidationResult with any issues found.
    """
    result = ValidationResult(is_valid=True)

    if not isinstance(data, dict):
        result.add_error("dispatch must be a JSON object")
        return result

    status = data.get("status")
    if status and status not in VALID_DISPATCH_STATUSES:
        result.add_error(
            f"Invalid dispatch status '{status}'. Must be one of: {VALID_DISPATCH_STATUSES}"
        )

    # Validate task_hint if present
    valid_tasks = {"genius", "ctx", "dev", "review", "issue", "issue-reviewer", "wait_for_human"}
    if data.get("task_hint") and data["task_hint"] not in valid_tasks:
        result.add_error(
            f"Invalid task_hint '{data['task_hint']}'. Must be one of: {valid_tasks}"
        )

    # Reuse agentRunConfig validators for shared fields
    config_result = validate_agent_run_config(json.dumps(data))
    # Filter out 'task' errors since dispatch uses 'task_hint' instead
    filtered = [e for e in config_result.errors if "missing required field: 'task'" not in e]
    for error in filtered:
        result.add_error(error)

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


# Maximum retries before escalating to genius
MAX_RETRIES = 2


def should_retry_agent(comment_body: str, role: str, retry_count: int = 0) -> dict:
    """Determine if an agent comment is missing its expected <agentRunConfig> and should be retried.

    Args:
        comment_body: The agent's output comment text.
        role: The agent role name (e.g., "dev", "ctx", "review").
        retry_count: Number of times this agent has already been retried.

    Returns:
        Dict with keys:
        - RETRY: "true" or "false"
        - AGENT: agent name to retry (if RETRY=true)
        - ESCALATE: "true" if retries exhausted, escalate to genius
    """
    configs = extract_agent_run_configs(comment_body)
    if configs:
        # Has agentRunConfig — check if it's wait_for_human (intentional stop)
        for config_str in configs:
            try:
                config = json.loads(config_str)
                if config.get("task") == "wait_for_human":
                    return {"RETRY": "false"}  # Intentional stop, no retry needed
            except json.JSONDecodeError:
                pass
        return {"RETRY": "false"}  # Has proper hand-off to another agent

    # No agentRunConfig found — agent output is incomplete, should retry
    if retry_count >= MAX_RETRIES:
        # Escalate to genius after max retries
        return {"RETRY": "true", "AGENT": "genius", "ESCALATE": "true"}

    return {"RETRY": "true", "AGENT": role, "ESCALATE": "false"}


def main():
    """CLI entry point: validate output from stdin or file."""
    if len(sys.argv) < 2:
        print("Usage: python validate_output.py <command> [options]")
        print("Commands:")
        print("  validate <file> [role]      Validate agent output")
        print("  check-retry --comment-file <file> --role <role> [--retry-count N]")
        print("    Check if agent output should be retried")
        sys.exit(1)

    command = sys.argv[1]

    if command == "check-retry":
        # Parse arguments for check-retry
        comment_file = ""
        role = ""
        retry_count = 0

        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--comment-file" and i + 1 < len(sys.argv):
                comment_file = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--role" and i + 1 < len(sys.argv):
                role = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--retry-count" and i + 1 < len(sys.argv):
                retry_count = int(sys.argv[i + 1])
                i += 2
            else:
                i += 1

        if not comment_file or not role:
            print("ERROR: --comment-file and --role are required for check-retry")
            sys.exit(1)

        with open(comment_file, "r", encoding="utf-8") as f:
            comment_body = f.read()

        result = should_retry_agent(comment_body, role, retry_count)

        for key, value in result.items():
            print(f"{key}={value}")

    elif command == "validate":
        filepath = sys.argv[2] if len(sys.argv) > 2 else ""
        role = sys.argv[3] if len(sys.argv) > 3 else ""

        if not filepath:
            print("ERROR: file path required for validate")
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

    else:
        # Backward compatibility: treat first arg as file path (validate mode)
        filepath = sys.argv[1]
        role = sys.argv[2] if len(sys.argv) > 2 else ""

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
