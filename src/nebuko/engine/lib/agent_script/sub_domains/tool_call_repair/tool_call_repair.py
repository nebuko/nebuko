"""Repair malformed LLM tool-call arguments before the SDK rejects them.

Measured on the GLM-5.3-flash context run (issue #1114 console log), 13% of
all model actions died in Pydantic/JSON validation — ``extra_forbidden`` on
glue keys, unparseable JSON from unterminated strings — and each failure cost
a full LLM round-trip (the SDK re-feeds the whole context plus the error
message, ~30K input tokens per miss). The SDK already repairs the common
malformations it knows about (``fix_malformed_tool_arguments``,
``sanitize_json_control_chars``); this module adds the layers it lacks:

1. **Truncated JSON.** A call whose arguments string ends mid-string or
   mid-value is cut back to the last complete element and closed, instead of
   being reported as "unparseable JSON".
2. **Glue keys.** Models that narrate before calling sometimes weld prose into
   the JSON, producing keys like ``"defaults\\nsummary"`` or
   ``'"commands": [], "summary": "noop"}'``. No legitimate tool parameter name
   contains a control character, a quote or a bracket, so any such key is
   dropped at parse time.
3. **Unknown and misspelled keys.** ``extra="forbid"`` on the action schemas
   turns a stray key into a failed action; unknown keys are dropped, and an
   unknown key that is an unambiguous prefix of exactly one declared field
   (``new`` -> ``new_str``) is renamed instead.

The hooks are the SDK's own seams: ``parse_tool_call_arguments`` and
``fix_malformed_tool_arguments`` are looked up as module globals inside
``openhands.sdk.agent.agent`` at call time, and
``ToolDefinition.action_from_arguments`` is the last point where the arguments
dict is still whole (response-schema fields are already split out there), so
patching those three names covers every tool — SDK, builtin and engine-owned —
without touching the SDK package itself.
"""

from __future__ import annotations

import json
import re
from typing import Any

from openhands.sdk import get_logger
from openhands.sdk.agent import agent as _sdk_agent
from openhands.sdk.agent import utils as _sdk_utils
from openhands.sdk.tool.tool import ToolDefinition

logger = get_logger(__name__)

_original_parse = _sdk_utils.parse_tool_call_arguments
_original_fix = _sdk_utils.fix_malformed_tool_arguments
_original_action_from_arguments = ToolDefinition.action_from_arguments

#: No legitimate tool parameter name contains a control character, a quote,
#: a bracket, a brace or a colon. A key that does is welded-in prose, not a
#: parameter — GLM-5.3-flash emits them when its free-text "Summary:" line is
#: not cleanly separated from the function-call JSON.
_GARBAGE_KEY_RE = re.compile(r"[\x00-\x20\"'{}\[\]:]")

#: True once :func:`install` has patched the SDK seams in this process.
_installed = False


def parse_tool_call_arguments(raw_arguments: str) -> dict[str, Any]:
    """SDK-compatible parse, plus truncation and glue-key repair.

    Raises ``json.JSONDecodeError`` when the string stays unparseable after
    every repair layer, exactly like the SDK original, so the SDK's error
    handling (the ``_emit_tool_error`` path) still runs for the truly lost
    calls.
    """
    try:
        return _strip_garbage_keys(_original_parse(raw_arguments))
    except json.JSONDecodeError:
        # The SDK's own fallback (control-char sanitizing) already ran inside
        # the original and still failed — try the truncation repairs before
        # letting the SDK's invalid-tool-call path see the error.
        repaired = _repair_truncated_json(raw_arguments)
        if repaired is None:
            raise
        # Mirror the SDK's normalize step: drop None values, fix nothing else.
        return _strip_garbage_keys(
            {key: value for key, value in repaired.items() if value is not None}
        )


def _strip_garbage_keys(arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop keys that cannot be parameter names (glued prose, control chars)."""
    return {
        key: value
        for key, value in arguments.items()
        if isinstance(key, str) and not _GARBAGE_KEY_RE.search(key)
    }


def _repair_truncated_json(raw_arguments: str) -> dict[str, Any] | None:
    """Close a truncated arguments string and parse it, or return None.

    Two shapes are repaired, in order of preference:

    * the string ends inside a JSON string value or key — it is closed (a key
      gets an empty value) and the open containers are closed around it;
    * the string ends mid-element — it is cut back to the last complete
      element boundary and the still-open containers are closed.
    """
    sanitized = _sdk_utils.sanitize_json_control_chars(raw_arguments)
    for candidate in _repair_candidates(sanitized):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            logger.info("tool_call_repair: recovered truncated tool arguments")
            return parsed
    return None


def _repair_candidates(sanitized: str):
    """Yield plausible closings of a truncated JSON object, best first."""
    yield from _close_eof_string(sanitized)
    yield from _close_at_boundaries(sanitized)


def _close_eof_string(sanitized: str):
    """Candidates for a string that is still open at end of input."""
    in_str = False
    escaped = False
    string_start = -1
    for index, char in enumerate(sanitized):
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
        elif char == '"':
            in_str = True
            string_start = index
    if not in_str:
        return
    before = sanitized[:string_start].rstrip()
    preceding = before[-1] if before else ""
    if preceding == ":":
        # A truncated value: close the string, then the containers.
        yield _close_containers(sanitized + '"')
    else:
        # A truncated key: close it and give it an empty value.
        yield _close_containers(sanitized + '": ""')


def _close_at_boundaries(sanitized: str):
    """Candidates cut at the last complete element, newest boundary first."""
    boundaries = _element_boundaries(sanitized)
    for cut, stack in reversed(boundaries):
        prefix = sanitized[:cut].rstrip()
        if prefix.endswith(","):
            prefix = prefix[:-1].rstrip()
        yield _close_containers(prefix, stack)


def _element_boundaries(sanitized: str) -> list[tuple[int, list[str]]]:
    """Record (index-after, open-containers) at each complete element end.

    An element end is a matched ``}`` / ``]``, or the closing quote of a
    *value* string inside an open container. Key strings are not boundaries —
    cutting after a key would leave ``{"key"`` dangling.
    """
    stack: list[str] = []
    boundaries: list[tuple[int, list[str]]] = []
    in_str = False
    escaped = False
    for index, char in enumerate(sanitized):
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
                if stack and stack[-1] == "[":
                    boundaries.append((index + 1, list(stack)))
                elif stack and stack[-1] == "{" and _expecting_value(sanitized, index):
                    boundaries.append((index + 1, list(stack)))
            continue
        if char == '"':
            in_str = True
        elif char in "[{":
            stack.append(char)
        elif char in "}]":
            if stack:
                stack.pop()
            boundaries.append((index + 1, list(stack)))
    return boundaries


def _expecting_value(sanitized: str, close_index: int) -> bool:
    """True when the string closed at ``close_index`` is a value, not a key.

    A key string is directly preceded (ignoring whitespace) by ``{`` or ``,``
    inside its object; anything else — a ``:`` — means value.
    """
    before = sanitized[:close_index - 1].rstrip()
    return not (before.endswith("{") or before.endswith(","))


def _close_containers(prefix: str, stack: list[str] | None = None) -> str:
    """Close the still-open containers of ``prefix`` in reverse order."""
    if stack is None:
        stack = _open_containers(prefix)
    return prefix + "".join("}" if frame == "{" else "]" for frame in reversed(stack))


def _open_containers(text: str) -> list[str]:
    stack: list[str] = []
    in_str = False
    escaped = False
    for char in text:
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
        elif char == '"':
            in_str = True
        elif char in "[{":
            stack.append(char)
        elif char in "}]":
            if stack:
                stack.pop()
    return stack


def fix_malformed_tool_arguments(
    arguments: dict[str, Any], action_type: Any
) -> dict[str, Any]:
    """SDK repair pass plus the glue-key strip (fix 2's key layer)."""
    fixed = _original_fix(arguments, action_type)
    if not isinstance(fixed, dict):
        return fixed
    return _strip_garbage_keys(fixed)


def action_from_arguments(self, arguments: dict[str, Any]):
    """``ToolDefinition.action_from_arguments`` with unknown-key repair.

    Runs after the SDK has split response-schema fields out, so every key left
    must be an action field: unknown keys are dropped, and an unknown key that
    is an unambiguous prefix of exactly one declared field is renamed to it
    (GLM writes ``new`` where ``new_str`` is declared).
    """
    action_arguments, structured_output = self._split_response_arguments(
        arguments
    )
    action = self.action_type.model_validate(
        _repair_keys(action_arguments, self.action_type)
    )
    action._structured_output = structured_output
    return action


def _repair_keys(
    arguments: dict[str, Any], action_type: type
) -> dict[str, Any]:
    """Drop unknown keys; rename unambiguous prefix misspellings."""
    fields = {
        name
        for name, info in getattr(action_type, "model_fields", {}).items()
        if not info.alias
    } | {
        info.alias
        for info in getattr(action_type, "model_fields", {}).values()
        if info.alias
    }
    if not fields:
        return arguments
    repaired: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in fields:
            repaired[key] = value
            continue
        prefixes = [name for name in fields if name.startswith(key)]
        if len(prefixes) == 1:
            repaired[prefixes[0]] = value
        else:
            logger.info(f"tool_call_repair: dropped unknown key {key!r}")
    return repaired


def install() -> None:
    """Patch the SDK's tool-argument seams. Idempotent per process."""
    global _installed
    if _installed:
        return
    _sdk_agent.parse_tool_call_arguments = parse_tool_call_arguments
    _sdk_agent.fix_malformed_tool_arguments = fix_malformed_tool_arguments
    _sdk_utils.parse_tool_call_arguments = parse_tool_call_arguments
    _sdk_utils.fix_malformed_tool_arguments = fix_malformed_tool_arguments
    ToolDefinition.action_from_arguments = action_from_arguments
    _installed = True
    logger.info("tool_call_repair: installed JSON and schema repair hooks")
